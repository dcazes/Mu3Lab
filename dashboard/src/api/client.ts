export type ApiErrorKind = 'http' | 'signed_out' | 'offline' | 'timeout';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind = 'http',
    readonly status = 0,
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

type SessionListener = () => void;
const sessionListeners = new Set<SessionListener>();

/** Called when Authentik redirects an API call to its login page. */
export function onSignedOut(listener: SessionListener) {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

/** The CSRF token is bound to the Authentik session; forget it when that changes. */
function forgetSession() {
  csrfToken = null;
}

// No request waits forever: a stuck one would otherwise hold the dashboard's refresh (review R22).
export const READ_TIMEOUT_MS = 60_000;
// Changes get longer, and a timed-out change may still finish; its idempotency key stops a repeat acting twice.
export const CHANGE_TIMEOUT_MS = 300_000;

async function request(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const deadline = new AbortController();
  // Never cleared: it stays armed until the body is read too, then fires harmlessly.
  setTimeout(() => deadline.abort(), timeoutMs);
  const caller = init.signal;
  const forward = () => deadline.abort();
  caller?.addEventListener('abort', forward);
  if (caller?.aborted) deadline.abort();
  let res: Response;
  try {
    // A manual redirect exposes Authentik's login redirect instead of an opaque CORS failure.
    res = await fetch(path, { ...init, signal: deadline.signal, redirect: 'manual' });
  } catch (cause) {
    if (caller?.aborted) throw cause;
    if (deadline.signal.aborted)
      throw new ApiError(
        init.method && init.method !== 'GET'
          ? 'Mu3Lab did not answer in time. Check whether the change happened before trying again.'
          : 'Mu3Lab did not answer in time.',
        'timeout',
      );
    throw new ApiError('Mu3Lab is unreachable.', 'offline');
  } finally {
    caller?.removeEventListener('abort', forward);
  }
  if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
    forgetSession();
    sessionListeners.forEach((listener) => listener());
    throw new ApiError('Your session expired. Sign in again.', 'signed_out', res.status);
  }
  return res;
}

async function failure(res: Response, method: string, path: string): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    /* use the HTTP fallback message */
  }
  const error = body.error as string | { message?: string; recommended_action?: string } | undefined;
  const message =
    typeof error === 'string'
      ? error
      : error?.message
        ? `${error.message}${error.recommended_action ? ` ${error.recommended_action}` : ''}`
        : `${method} ${path}: HTTP ${res.status}`;
  return new ApiError(message, 'http', res.status, body);
}

export async function api<T>(path: string, signal?: AbortSignal, timeoutMs = READ_TIMEOUT_MS): Promise<T> {
  const res = await request(path, { headers: { Accept: 'application/json' }, signal }, timeoutMs);
  if (!res.ok) throw await failure(res, 'GET', path);
  return res.json() as Promise<T>;
}

let csrfToken: Promise<string> | null = null;
let csrfFetchedAt = 0;
// Authentik renews its session token while the dashboard stays open; never
// keep a CSRF token much longer than the session evidence it was made from.
const CSRF_MAX_AGE_MS = 10 * 60 * 1000;

function csrf(): Promise<string> {
  if (csrfToken && Date.now() - csrfFetchedAt > CSRF_MAX_AGE_MS) csrfToken = null;
  if (!csrfToken) {
    csrfFetchedAt = Date.now();
    csrfToken = api<{ csrf_token: string }>('/api/v1/session')
      .then((result) => result.csrf_token)
      .catch((error) => {
        csrfToken = null;
        throw error;
      });
  }
  return csrfToken;
}

async function mutate<T>(method: string, path: string, body?: unknown, idempotent = true): Promise<T> {
  // One key for every attempt, so a retry after a token refresh cannot act twice.
  const idempotencyKey = idempotent ? crypto.randomUUID() : '';
  const send = async () => {
    const headers: Record<string, string> = { Accept: 'application/json', 'X-Mu3Lab-CSRF': await csrf() };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    return request(
      path,
      { method, headers, body: body === undefined ? undefined : JSON.stringify(body) },
      CHANGE_TIMEOUT_MS,
    );
  };
  let res = await send();
  if (res.status === 403) {
    const error = await failure(res.clone(), method, path);
    if (error.body.code === 'csrf_failed') {
      // The session was renewed since the token was issued: fetch a new one once.
      forgetSession();
      res = await send();
    }
  }
  if (!res.ok) throw await failure(res, method, path);
  return res.json() as Promise<T>;
}

export const postApi = <T>(path: string) => mutate<T>('POST', path);
export const postJsonApi = <T>(path: string, body: unknown) => mutate<T>('POST', path, body);
export const putJsonApi = <T>(path: string, body: unknown) => mutate<T>('PUT', path, body, false);
export const deleteApi = <T>(path: string, body?: unknown) => mutate<T>('DELETE', path, body);
