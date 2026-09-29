import type { ProviderCatalogItem } from '../../api';

export interface Detection {
  provider: ProviderCatalogItem;
  /** A distinctive prefix is certain; a shape match is a best guess the owner should confirm. */
  certain: boolean;
}

const MAX_KEY_LENGTH = 256;

/**
 * Catch pastes that cannot be an API key, usually the wrong clipboard: a
 * sentence, a link or several lines. Mirrors ctl/provider_catalog.py `key_problem`.
 */
export function keyProblem(key: string): string {
  const value = key.trim();
  if (!value) return '';
  if (/\s/.test(value) || value.includes('://') || value.length > MAX_KEY_LENGTH) {
    return "That doesn't look like an API key. Copy the key itself from the provider's keys page and paste again.";
  }
  return '';
}

/** Guess the provider from the key itself; mirrors ctl/provider_catalog.py `detect`. */
export function detectProvider(key: string, catalog: ProviderCatalogItem[]): Detection | null {
  const value = key.trim();
  if (!value || keyProblem(value)) return null;
  const byPrefix = catalog
    .flatMap((provider) => (provider.prefixes ?? []).map((prefix) => ({ provider, prefix })))
    .filter(({ prefix }) => value.startsWith(prefix))
    .sort((a, b) => b.prefix.length - a.prefix.length);
  if (byPrefix.length) return { provider: byPrefix[0].provider, certain: true };
  const byShape = catalog.filter((item) => item.key_pattern && new RegExp(item.key_pattern).test(value));
  return byShape.length === 1 ? { provider: byShape[0], certain: false } : null;
}

// --- OpenRouter: get a key by signing in (OAuth PKCE) -------------------------------

const OPENROUTER_AUTH = 'https://openrouter.ai/auth';
const VERIFIER_KEY = 'mu3lab.openrouter.verifier';
export const OAUTH_RETURN_PARAM = 'provider_oauth';

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/** Where OpenRouter sends the owner back: this page, marked so we know to finish up. */
export function openRouterCallback(): string {
  return `${window.location.origin}/settings/ai?${OAUTH_RETURN_PARAM}=openrouter`;
}

/** Build the sign-in URL and remember the verifier for this tab only. */
export async function openRouterSignInUrl(): Promise<string> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(
    new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))),
  );
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const params = new URLSearchParams({
    callback_url: openRouterCallback(),
    code_challenge: challenge,
    code_challenge_method: 'S256',
    key_label: 'Mu3Lab',
  });
  return `${OPENROUTER_AUTH}?${params}`;
}

/**
 * If this page load is OpenRouter sending the owner back, return the one-time
 * code and verifier (each usable once) and tidy the address bar. Either may be
 * empty when the sign-in was cancelled or started in another tab.
 */
export function takeOpenRouterReturn(): { code: string; code_verifier: string } | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get(OAUTH_RETURN_PARAM) !== 'openrouter') return null;
  const code = params.get('code') || '';
  let verifier = '';
  try {
    verifier = sessionStorage.getItem(VERIFIER_KEY) || '';
    sessionStorage.removeItem(VERIFIER_KEY);
  } catch {
    // Storage blocked: the sign-in cannot be finished; the owner can paste a key instead.
  }
  window.history.replaceState({}, '', window.location.pathname);
  return { code, code_verifier: verifier };
}
