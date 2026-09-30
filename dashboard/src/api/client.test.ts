import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('CSRF token handling', () => {
  it('fetches a new token once after the session was renewed, keeping the same idempotency key', async () => {
    let tokens = 0;
    const posts: Headers[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init: RequestInit) => {
        if (path === '/api/v1/session') return json(200, { csrf_token: `token-${++tokens}` });
        posts.push(new Headers(init.headers));
        return posts.length === 1
          ? json(403, { ok: false, error: 'same-origin CSRF verification failed', code: 'csrf_failed' })
          : json(200, { ok: true });
      }),
    );
    const { postApi } = await import('./client');
    await expect(postApi('/api/v1/things')).resolves.toEqual({ ok: true });
    expect(posts.map((headers) => headers.get('X-Mu3Lab-CSRF'))).toEqual(['token-1', 'token-2']);
    expect(posts[0].get('Idempotency-Key')).toBe(posts[1].get('Idempotency-Key'));
  });

  it('does not retry an ordinary permission refusal', async () => {
    const fetchMock = vi.fn(async (path: string) =>
      path === '/api/v1/session' ? json(200, { csrf_token: 't' }) : json(403, { ok: false, error: 'not allowed' }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { postApi } = await import('./client');
    await expect(postApi('/api/v1/things')).rejects.toThrow('not allowed');
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/v1/things')).toHaveLength(1);
  });
});
