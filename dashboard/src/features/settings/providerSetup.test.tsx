import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProviderCatalogItem, ProviderMetadata } from '../../api';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { AiSettings } from './AiSettings';
import { VaultSetupDialog } from './VaultSetup';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const catalogItem = (id: string, name: string, extra: Partial<ProviderCatalogItem> = {}): ProviderCatalogItem => ({
  id,
  name,
  key_hint: 'key…',
  prefix: '',
  instructions: '',
  example_models: [],
  signup_url: `https://${id}.example/`,
  keys_url: `https://${id}.example/keys`,
  account: 'email',
  free_tier: `${name} free tier.`,
  ...extra,
});

const catalog = [
  catalogItem('openrouter', 'OpenRouter', { prefix: 'sk-or-v1-', oauth: true, google_sign_in: true }),
  catalogItem('groq', 'Groq', { recommended: true, prefix: 'gsk_', google_sign_in: true }),
  catalogItem('cerebras', 'Cerebras', { recommended: true, prefix: 'csk-' }),
  catalogItem('mistral', 'Mistral', { key_pattern: '^[A-Za-z0-9]{32}$' }),
];

const connection = (id: string, name: string): ProviderMetadata => ({
  id,
  name,
  label: name,
  enabled: true,
  state: 'verified',
  key_hint: '',
  credential_indicator: '',
  model_samples: [],
  models_are_examples: true,
  last_attempt_at: '',
  last_verified_at: '',
  updated_at: '',
  active_job_id: '',
  error: '',
  supported: true,
});

function providersApi(connected: ProviderMetadata[], recommendedVerified: string[]) {
  return (path: string) => {
    if (path === '/api/v1/providers/catalog') return { ok: true, providers: catalog, recommended_minimum: 2 };
    if (path === '/api/v1/providers')
      return {
        ok: true,
        providers: connected,
        setup: {
          verified: connected.length,
          complete: connected.length > 0,
          recommended: ['groq', 'cerebras'],
          recommended_verified: recommendedVerified,
          recommended_minimum: 2,
          recommendation_met: recommendedVerified.length >= 2,
        },
      };
    return undefined;
  };
}

describe('AI provider checklist', () => {
  it('lists recommended providers first with sign-up and key links', async () => {
    stubFetch(providersApi([], []));
    renderWithDashboard(<AiSettings />, dashboardData([]));
    expect(await screen.findByText('Connect one provider to finish setup')).toBeInTheDocument();
    const rows = screen.getAllByRole('link', { name: /Get key/ });
    expect(rows.map((link) => link.getAttribute('href'))).toEqual([
      'https://groq.example/keys',
      'https://cerebras.example/keys',
      'https://mistral.example/keys',
    ]);
    expect(screen.getAllByText('Recommended')).toHaveLength(2);
    expect(screen.getAllByText('One-click Google sign-in')).toHaveLength(2);
    // OpenRouter hands over a key through its own sign-in page: no key to copy.
    expect(screen.getByRole('button', { name: /Connect with OpenRouter/ })).toBeInTheDocument();
  });

  it('accepts one provider but recommends a backup', async () => {
    stubFetch(providersApi([connection('groq', 'Groq')], ['groq']));
    renderWithDashboard(<AiSettings />, dashboardData([]));
    expect(await screen.findByText('Chat works — add a backup provider')).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 done/)).toBeInTheDocument();
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('stops nagging once two recommended providers work', async () => {
    stubFetch(providersApi([connection('groq', 'Groq'), connection('cerebras', 'Cerebras')], ['groq', 'cerebras']));
    renderWithDashboard(<AiSettings />, dashboardData([]));
    expect(await screen.findByText('More free providers')).toBeInTheDocument();
    expect(screen.queryByText('Chat works — add a backup provider')).not.toBeInTheDocument();
  });
});

describe('One key field for every provider', () => {
  const posted = (fetchMock: ReturnType<typeof stubFetch>, path: string) =>
    fetchMock.mock.calls
      .filter(([url, init]) => url === path && init?.method === 'POST')
      .map(([, init]) => JSON.parse(String(init?.body)));

  const api = (path: string, init?: RequestInit) =>
    init?.method === 'POST' ? { ok: true, job: { id: 'job-1' } } : providersApi([], [])(path);

  it('detects the provider from a pasted key and connects it straight away', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    fireEvent.paste(await screen.findByLabelText('API key'), { clipboardData: { getData: () => ' gsk_abc ' } });
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers')).toEqual([{ provider_id: 'groq', api_key: 'gsk_abc' }]),
    );
  });

  it('asks which provider a key is for when it cannot tell', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'mystery-key' } });
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Which provider is this key for/), { target: { value: 'cerebras' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers')).toEqual([{ provider_id: 'cerebras', api_key: 'mystery-key' }]),
    );
  });

  it('offers a best guess for keys without a prefix but waits for the owner', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    fireEvent.paste(await screen.findByLabelText('API key'), { clipboardData: { getData: () => 'A1b2'.repeat(8) } });
    expect(await screen.findByText(/Looks like a Mistral key/)).toBeInTheDocument();
    expect(posted(fetchMock, '/api/v1/providers')).toEqual([]);
  });

  it('finishes the OpenRouter sign-in when OpenRouter sends the owner back', async () => {
    const fetchMock = stubFetch(api);
    sessionStorage.setItem('mu3lab.openrouter.verifier', 'v'.repeat(43));
    window.history.pushState({}, '', '/settings/ai?provider_oauth=openrouter&code=one-time');
    renderWithDashboard(<AiSettings />, dashboardData([]));
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers/openrouter/oauth')).toEqual([
        { code: 'one-time', code_verifier: 'v'.repeat(43) },
      ]),
    );
    expect(window.location.search).toBe('');
    expect(sessionStorage.getItem('mu3lab.openrouter.verifier')).toBeNull();
  });
});

describe('Get started', () => {
  const api =
    (seeded: boolean, connected: ProviderMetadata[], browsers: string[] = []) =>
    (path: string) =>
      path === '/api/v1/vault/status'
        ? { ok: true, seeded, seeded_at: '', browser_extension: { browsers, server_url: '' } }
        : (providersApi(connected, [])(path) ?? { handoffs: [] });

  beforeEach(() => localStorage.clear());

  it('points to the first unfinished step in order', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    const first = await screen.findByRole('link', { name: /1\. Connect a free AI provider/ });
    expect(first).toHaveAttribute('href', '/settings/ai');
    expect(first).toHaveClass('is-next');
    expect(screen.getByText('0 of 5 done')).toBeInTheDocument();
    expect(screen.queryByText(/Save your app logins/)).not.toBeInTheDocument();
  });

  it('ticks off detected steps and adds the vault step when it was never done', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(false, [connection('groq', 'Groq')]));
    renderWithDashboard(<HomePage />, dashboardData([]));
    expect(await screen.findByText('1 of 6 done')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Save your app logins to your password vault/ })).toHaveClass('is-next');
  });

  it('opens a numbered guide for steps it cannot detect and remembers them', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    fireEvent.click(await screen.findByRole('button', { name: /Let your browser fill in your passwords/ }));
    expect(screen.getByText(/choose/)).toHaveTextContent('Self-hosted');
    fireEvent.click(screen.getByRole('button', { name: "I've done this" }));
    expect(await screen.findByText('1 of 5 done')).toBeInTheDocument();
  });

  it('skips the server setup when the installer already added Bitwarden', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, [], ['Google Chrome']));
    renderWithDashboard(<HomePage />, dashboardData([]));
    fireEvent.click(await screen.findByRole('button', { name: /Let your browser fill in your passwords/ }));
    expect(screen.getByText(/already added Bitwarden to Google Chrome/)).toBeInTheDocument();
    expect(screen.queryByText(/Self-hosted/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Get the extension' })).not.toBeInTheDocument();
  });
});

describe('Vault setup', () => {
  it('sends the master password once and shows what was saved', async () => {
    const fetchMock = stubFetch((path) =>
      path === '/api/v1/vault/setup'
        ? { ok: true, created: ['Groq', 'FreeLLMAPI dashboard'], updated: [], unchanged: [], skipped: ['Authentik'] }
        : undefined,
    );
    renderWithDashboard(<VaultSetupDialog open onClose={() => undefined} />, dashboardData([]));
    fireEvent.change(screen.getByLabelText('Vaultwarden email'), { target: { value: 'me@example.test' } });
    fireEvent.change(screen.getByLabelText('Master password'), { target: { value: 'hunter2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to vault' }));
    expect(await screen.findByText('2 logins saved')).toBeInTheDocument();
    expect(screen.getByText(/Already in your vault, left untouched: Authentik/)).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([path]) => path === '/api/v1/vault/setup');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      email: 'me@example.test',
      master_password: 'hunter2',
      totp: '',
    });
  });

  it('asks for a two-step code when the vault requires one', async () => {
    let attempts = 0;
    stubFetch((path) => {
      if (path !== '/api/v1/vault/setup') return undefined;
      attempts += 1;
      return attempts === 1
        ? new Response(JSON.stringify({ ok: false, error: 'Enter the current code.', code: 'two_factor_required' }), {
            status: 401,
          })
        : { ok: true, created: [], updated: [], unchanged: ['Groq'], skipped: [] };
    });
    renderWithDashboard(<VaultSetupDialog open onClose={() => undefined} />, dashboardData([]));
    fireEvent.change(screen.getByLabelText('Vaultwarden email'), { target: { value: 'me@example.test' } });
    fireEvent.change(screen.getByLabelText('Master password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to vault' }));
    const code = await screen.findByLabelText('Two-step login code');
    expect(screen.getByText('Enter the current code.')).toBeInTheDocument();
    // The password field is cleared after every attempt and must be re-entered.
    expect(screen.getByLabelText('Master password')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Master password'), { target: { value: 'pw' } });
    fireEvent.change(code, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to vault' }));
    await waitFor(() => expect(screen.getByText('Your vault is up to date')).toBeInTheDocument());
    expect(within(document.body).queryByLabelText('Master password')).not.toBeInTheDocument();
  });
});
