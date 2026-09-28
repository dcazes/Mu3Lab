import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  catalogItem('openrouter', 'OpenRouter'),
  catalogItem('groq', 'Groq', { recommended: true }),
  catalogItem('cerebras', 'Cerebras', { recommended: true }),
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
      'https://openrouter.example/keys',
    ]);
    expect(screen.getAllByText('Recommended')).toHaveLength(2);
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
