import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProviderCatalogItem, ProviderMetadata } from '../../api';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
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
  prefixes: [],
  instructions: '',
  signup_url: `https://${id}.example/`,
  keys_url: `https://${id}.example/keys`,
  account: 'email',
  free_tier: `${name} free tier.`,
  ...extra,
});

const catalog = [
  catalogItem('openrouter', 'OpenRouter', { prefixes: ['sk-or-v1-'] }),
  catalogItem('groq', 'Groq', { recommended: true, prefixes: ['gsk_'] }),
  catalogItem('cerebras', 'Cerebras', { prefixes: ['csk-'], payment_required: true }),
  catalogItem('nvidia', 'NVIDIA', { recommended: true, prefixes: ['nvapi-'] }),
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
          recommended: ['groq', 'nvidia'],
          recommended_verified: recommendedVerified,
          recommended_minimum: 2,
          recommendation_met: recommendedVerified.length >= 2,
        },
      };
    return undefined;
  };
}

describe('AI provider checklist', () => {
  it('lists recommended providers first with one key link each', async () => {
    stubFetch(providersApi([], []));
    renderWithDashboard(<AiSettings />, dashboardData([]));
    expect(await screen.findByText('Connect one provider to finish setup')).toBeInTheDocument();
    const rows = screen.getAllByRole('link', { name: /Get key/ });
    expect(rows.map((link) => link.getAttribute('href'))).toEqual([
      'https://groq.example/keys',
      'https://nvidia.example/keys',
      'https://openrouter.example/keys',
      'https://cerebras.example/keys',
      'https://mistral.example/keys',
    ]);
    expect(screen.getAllByText('Recommended')).toHaveLength(2);
    expect(screen.getAllByText('Payment method required')).toHaveLength(1);
    expect(screen.queryByText(/Google sign-in/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Sign up/ })).not.toBeInTheDocument();
  });

  it('accepts one provider but recommends a backup', async () => {
    stubFetch(providersApi([connection('groq', 'Groq')], ['groq']));
    renderWithDashboard(<AiSettings />, dashboardData([]));
    expect(await screen.findByText('Chat works — add a backup provider')).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 connected/)).toBeInTheDocument();
    // Groq now lives under Connected providers, so the checklist no longer offers it.
    const keyLinks = screen.getAllByRole('link', { name: /Get key/ }).map((link) => link.getAttribute('href'));
    expect(keyLinks).not.toContain('https://groq.example/keys');
    expect(keyLinks).toHaveLength(4);
  });

  it('stops nagging once two recommended providers work', async () => {
    stubFetch(providersApi([connection('groq', 'Groq'), connection('nvidia', 'NVIDIA')], ['groq', 'nvidia']));
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

  it('shows the pasted key and waits for Connect', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    const input = await screen.findByLabelText('API key');
    fireEvent.paste(input, { clipboardData: { getData: () => ' gsk_abc ' } });
    expect(input).toHaveValue('gsk_abc');
    expect(input).not.toHaveAttribute('type', 'password');
    expect(screen.getByText('Groq key')).toBeInTheDocument();
    expect(posted(fetchMock, '/api/v1/providers')).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers')).toEqual([{ provider_id: 'groq', api_key: 'gsk_abc' }]),
    );
  });

  it('lets the owner change a recognised provider', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    fireEvent.paste(await screen.findByLabelText('API key'), { clipboardData: { getData: () => 'gsk_abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Not Groq? Change' }));
    fireEvent.change(screen.getByLabelText(/Which provider is this key for/), { target: { value: 'cerebras' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers')).toEqual([{ provider_id: 'cerebras', api_key: 'gsk_abc' }]),
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

  it('never blocks an unusual paste; it warns and asks for the provider', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    const text = 'CEREBRAS_API_KEY = csk-abc';
    fireEvent.paste(await screen.findByLabelText('API key'), { clipboardData: { getData: () => text } });
    expect(screen.getByText(/spaces or line breaks/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Which provider is this key for/), { target: { value: 'cerebras' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(posted(fetchMock, '/api/v1/providers')).toEqual([{ provider_id: 'cerebras', api_key: text }]),
    );
  });

  it('offers a best guess for keys without a prefix but waits for the owner', async () => {
    const fetchMock = stubFetch(api);
    renderWithDashboard(<AiSettings />, dashboardData([]));
    fireEvent.paste(await screen.findByLabelText('API key'), { clipboardData: { getData: () => 'A1b2'.repeat(8) } });
    expect(await screen.findByText(/Looks like a Mistral key/)).toBeInTheDocument();
    expect(posted(fetchMock, '/api/v1/providers')).toEqual([]);
  });

  it('says clearly when it picked a copied key up from the clipboard, without saving it', async () => {
    const fetchMock = stubFetch(api);
    vi.stubGlobal('navigator', {
      ...navigator,
      permissions: { query: async () => ({ state: 'granted' }) },
      clipboard: { readText: async () => 'csk-copied' },
    });
    renderWithDashboard(<AiSettings />, dashboardData([]));
    // The pickup only takes a key for the provider just opened.
    fireEvent.click(
      (await screen.findAllByRole('link', { name: /Get key/ })).find(
        (link) => link.getAttribute('href') === 'https://cerebras.example/keys',
      )!,
    );
    fireEvent.focus(window);
    expect(await screen.findByText(/Picked up your copied Cerebras key/)).toBeInTheDocument();
    expect(screen.getByLabelText('API key')).toHaveValue('csk-copied');
    expect(posted(fetchMock, '/api/v1/providers')).toEqual([]);
  });
});

describe('Get started', () => {
  const api =
    (seeded: boolean, connected: ProviderMetadata[], browsers: string[] = [], signedIn = false) =>
    (path: string) =>
      path === '/api/v1/vault/status'
        ? { ok: true, seeded, seeded_at: '', browser_extension: { browsers, server_url: '', signed_in: signedIn } }
        : (providersApi(connected, [])(path) ?? { handoffs: [] });

  beforeEach(() => localStorage.clear());

  it('points to the first unfinished step in order', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    const first = await screen.findByRole('button', { name: /1\. Add Bitwarden/ });
    expect(first.closest('.get-started-item')).toHaveClass('is-next');
    expect(screen.getByRole('link', { name: /2\. Connect an AI provider/ })).toHaveAttribute('href', '/settings/ai');
    expect(screen.getByRole('link', { name: /3\. Install your first personal app/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /4\. Use Mu3Lab on your phone or laptop/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /5\. Start chatting/ })).toHaveAttribute('href', '/chat');
    expect(screen.getByText('0 of 5 complete')).toBeInTheDocument();
    expect(screen.queryByText(/Save your app logins/)).not.toBeInTheDocument();
  });

  it('ticks off the chat step once LobeChat is opened from it', async () => {
    const { HomePage } = await import('../home/HomePage');
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    fireEvent.click(await screen.findByRole('link', { name: /Start chatting/ }));
    expect(await screen.findByText('1 of 5 complete')).toBeInTheDocument();
  });

  it('saves logins by itself and only asks the owner when saving is stuck', async () => {
    const { HomePage } = await import('../home/HomePage');
    const base = api(false, [connection('groq', 'Groq')]);
    stubFetch((path) => {
      const body = base(path);
      return path === '/api/v1/vault/status'
        ? { ...(body as object), automatic: { ok: false, error: 'Vaultwarden is not reachable.', people: [] } }
        : body;
    });
    renderWithDashboard(<HomePage />, dashboardData([]));
    expect(
      await screen.findByRole('link', { name: /Check why your app logins are not being saved/ }),
    ).toBeInTheDocument();
  });

  it('has no vault step while logins are being saved automatically', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(false, [connection('groq', 'Groq')]));
    renderWithDashboard(<HomePage />, dashboardData([]));
    expect(await screen.findByText('1 of 5 complete')).toBeInTheDocument();
    expect(screen.queryByText(/Save your app logins/)).not.toBeInTheDocument();
  });

  it('opens a numbered guide for steps it cannot detect and remembers them', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    fireEvent.click(await screen.findByRole('button', { name: /Use Mu3Lab on your phone or laptop/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Mark as complete' }));
    expect(await screen.findByText('1 of 5 complete')).toBeInTheDocument();
  });

  it('explains the Self-hosted server when Bitwarden has to be added by hand', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    renderWithDashboard(<HomePage />, dashboardData([]));
    fireEvent.click(await screen.findByRole('button', { name: /Add Bitwarden so your browser fills in/ }));
    expect(screen.getByText(/choose/)).toHaveTextContent('Self-hosted');
  });

  it('checks a new Bitwarden login without leaving the page', async () => {
    const { HomePage } = await import('../home/HomePage');
    let signedIn = false;
    stubFetch((path) => api(true, [], ['Google Chrome'], signedIn)(path));
    renderWithDashboard(<HomePage />, dashboardData([]));
    const check = await screen.findByRole('button', { name: 'Check Bitwarden login' });
    signedIn = true;
    fireEvent.click(check);
    expect(await screen.findByText('1 of 5 complete')).toBeInTheDocument();
    expect(screen.getByText(/Sign in to Bitwarden/).closest('.get-started-item')).toHaveClass('is-done');
  });

  it('ticks the Bitwarden step off by itself once the extension signs in', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, [], ['Google Chrome'], true));
    renderWithDashboard(<HomePage />, dashboardData([]));
    expect(await screen.findByText('1 of 5 complete')).toBeInTheDocument();
    expect(screen.getByText(/Sign in to Bitwarden/).closest('.get-started-item')).toHaveClass('is-done');
  });

  it('does not count the apps setup installs by itself as the first app', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, []));
    const preinstalled = [
      service('vaultwarden', 'Vaultwarden', 'foundation'),
      service('firecrawl', 'Firecrawl', 'optional'),
    ];
    renderWithDashboard(<HomePage />, dashboardData(preinstalled));
    expect(await screen.findByText('0 of 5 complete')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Install your first personal app/ })).toBeInTheDocument();
  });

  it('skips the server setup when the installer already added Bitwarden', async () => {
    const { HomePage } = await import('../home/HomePage');
    stubFetch(api(true, [], ['Google Chrome']));
    renderWithDashboard(<HomePage />, dashboardData([]));
    expect(await screen.findByText(/Click the shield icon next to the address bar/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Sign in to Bitwarden so your browser fills in/ }));
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
    expect(screen.getByText(/Already saved: Authentik/)).toBeInTheDocument();
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
