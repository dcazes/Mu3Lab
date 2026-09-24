import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AiMcpPanel, AppsPanel, HomePanel, ProtectionPanel } from './panels';
import type { Service, SystemResponse, TailscaleStatus } from './api';

function service(id: string, name: string, stage: Service['stage']): Service {
  return {
    id, name, stage, category: stage, lifecycle: 'optional', https_port: 1,
    maturity: stage === 'blocked' ? 'planned' : 'supported', auth: 'excluded', profiles: [], dependencies: [],
    availability: stage === 'blocked' ? 'blocked' : 'available', blocked_reason: '', route: 'pending', routable: false,
    required: stage !== 'optional', identity_note: '', resource_guidance: '', setup_action: '', mcp: { exposed: false, risk: '' },
    state: 'not_installed', lifecycle_state: 'not_installed', health_state: 'unknown', setup_state: 'pending', route_state: 'pending',
    identity_mode: '', backup_state: '', last_job_id: '', last_error: '', user_action: '', detail: '', url: '', route_ready: false, compose_present: true,
  };
}

const services = [service('ingress', 'Caddy', 'foundation'), service('ollama', 'Ollama', 'core'), service('nextcloud', 'Nextcloud', 'optional'), service('firecrawl', 'Firecrawl', 'optional'), service('planned-tool', 'Planned Tool', 'blocked')];
const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function systemResponse(overrides: Partial<SystemResponse> = {}): SystemResponse {
  return {
    ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '',
    memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {}, ...overrides,
  };
}

function tailscaleStatus(overrides: Partial<TailscaleStatus> = {}): TailscaleStatus {
  return {
    state: 'connected', backend_state: 'Running', online: true, dns_name: 'mu3lab-8.taile2cc7a.ts.net',
    detail: 'Connected to the tailnet.', serve: { state: 'available', ports: [443, 8443, 8446] }, ...overrides,
  };
}

describe('dashboard organization', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ state: 'not_connected', events: [], error: '' }) })); });
  afterEach(() => {
    cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
    if (originalClipboardDescriptor) Object.defineProperty(navigator, 'clipboard', originalClipboardDescriptor);
    else Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('groups Home apps and removes the duplicate wiring panel', () => {
    render(<HomePanel services={services} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByRole('heading', { name: 'Infrastructure' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'AI Integration' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Productivity apps' })).toBeInTheDocument();
    const firecrawl = screen.getByRole('tab', { name: /Firecrawl/ });
    expect(firecrawl.closest('.app-dock-group')?.querySelector('h3')).toHaveTextContent('AI Integration');
    expect(screen.queryByText('CORE WIRING')).not.toBeInTheDocument();
    expect(screen.queryByText('6/9')).not.toBeInTheDocument();
  });

  it('exposes Tailscale as an Infrastructure tab instead of a standalone link', () => {
    render(<HomePanel services={services} system={systemResponse({ tailscale: tailscaleStatus() })} jobs={{ ok: true, available: true, jobs: [] }} />);
    const tailscale = screen.getByRole('tab', { name: /Tailscale/ });
    expect(tailscale).toHaveAttribute('aria-selected', 'false');
    expect(tailscale).not.toHaveClass('selected');
    expect(tailscale.closest('.app-dock-group')?.querySelector('h3')).toHaveTextContent('Infrastructure');
    expect(screen.queryByRole('link', { name: 'Tailscale' })).not.toBeInTheDocument();
    fireEvent.click(tailscale);
    expect(tailscale).toHaveAttribute('aria-selected', 'true');
    expect(tailscale).toHaveClass('selected');
    expect(screen.getByRole('heading', { name: 'Tailscale' })).toBeInTheDocument();
  });

  it('maps live Tailscale state to green, red, and gray status dots', () => {
    const cases: Array<{ state: TailscaleStatus['state']; tone: string; label: string }> = [
      { state: 'connected', tone: 'green', label: 'Connected' },
      { state: 'disconnected', tone: 'red', label: 'Disconnected' },
      { state: 'unavailable', tone: 'gray', label: 'Status unavailable' },
    ];
    for (const item of cases) {
      const { unmount } = render(<HomePanel services={services} system={systemResponse({ tailscale: tailscaleStatus({ state: item.state }) })} jobs={{ ok: true, available: true, jobs: [] }} />);
      const tile = screen.getByRole('tab', { name: /Tailscale/ });
      const dot = tile.querySelector('.state-dot');
      expect(dot).toHaveClass(item.tone);
      expect(dot).toHaveAttribute('aria-label', item.label);
      unmount();
    }
  });

  it('shows Tailscale status facts, Serve routes, and the read-only Admin action', () => {
    const state = tailscaleStatus();
    render(<HomePanel services={services} system={systemResponse({ tailscale: state })} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Tailscale/ }));
    const inspector = document.querySelector('.tailscale-inspector');
    expect(inspector).toBeInTheDocument();
    expect(inspector).toHaveTextContent('INFRASTRUCTURE');
    expect(inspector).toHaveTextContent('Connected');
    expect(inspector).toHaveTextContent('BackendRunning');
    expect(inspector).toHaveTextContent('MagicDNSmu3lab-8.taile2cc7a.ts.net');
    expect(inspector).toHaveTextContent('Serve routes3 configured');
    expect(screen.getByText('HTTPS ports: 443, 8443, 8446')).toBeInTheDocument();
    const admin = screen.getByRole('link', { name: 'Open Tailscale Admin ↗' });
    expect(admin).toHaveAttribute('href', 'https://login.tailscale.com/admin');
    expect(admin).toHaveAttribute('target', '_blank');
    expect(admin).toHaveAttribute('rel', 'noreferrer');
  });

  it('copies exactly the normalized tailnet address and announces success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    render(<HomePanel services={services} system={systemResponse({ tailscale: tailscaleStatus() })} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Tailscale/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Copy tailnet address' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('mu3lab-8.taile2cc7a.ts.net'));
    expect(await screen.findByRole('status')).toHaveTextContent('Tailnet address copied.');
  });

  it('announces clipboard failure and hides copy when MagicDNS is absent', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { rerender } = render(<HomePanel services={services} system={systemResponse({ tailscale: tailscaleStatus() })} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Tailscale/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Copy tailnet address' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Could not copy the tailnet address.');
    rerender(<HomePanel services={services} system={systemResponse({ tailscale: tailscaleStatus({ dns_name: '' }) })} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.queryByRole('button', { name: 'Copy tailnet address' })).not.toBeInTheDocument();
  });

  it('keeps a manually selected Tailscale inspector through status refreshes and restores app actions', () => {
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional');
    nextcloud.state = 'ready';
    nextcloud.identity = { mode: 'local', state: 'ready', launch_url: 'https://example:8443', detail: '', last_verified_at: '', recovery_available: true, job_id: '' };
    nextcloud.ui = { state: 'ready', url: 'https://example:8443', label: 'Login', authentication: 'local', reason: '' };
    const { rerender } = render(<HomePanel services={[nextcloud]} system={systemResponse({ tailscale: tailscaleStatus() })} jobs={{ ok: true, available: true, jobs: [] }} />);
    const metricsBefore = document.querySelector('.home-metrics')?.textContent;
    fireEvent.click(screen.getByRole('tab', { name: /Tailscale/ }));
    rerender(<HomePanel services={[nextcloud]} system={systemResponse({ tailscale: tailscaleStatus({ state: 'disconnected', backend_state: 'NeedsLogin', online: false }) })} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByRole('tab', { name: /Tailscale/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Disconnected', { selector: '.status' })).toBeInTheDocument();
    expect(document.querySelector('.home-metrics')?.textContent).toBe(metricsBefore);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('heading', { name: 'Nextcloud' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Login ↗' })).toHaveAttribute('href', 'https://example:8443');
    expect(screen.getByRole('button', { name: 'Details & logs' })).toBeInTheDocument();
  });

  it('supports the legacy system response with and without a tailnet DNS name', () => {
    const { rerender } = render(<HomePanel services={services} system={systemResponse({ tailnet_dns_name: 'legacy.ts.net' })} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Tailscale/ }));
    expect(screen.getByText('Connected', { selector: '.status' })).toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.getByText('legacy.ts.net')).toBeInTheDocument();
    expect(screen.getByText('Tailscale Serve route status is unavailable.')).toBeInTheDocument();
    rerender(<HomePanel services={services} system={systemResponse()} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByText('Status unavailable', { selector: '.status' })).toBeInTheDocument();
  });

  it('defaults to Tailscale only when there are no visible registry services', () => {
    render(<HomePanel services={[service('planned-tool', 'Planned Tool', 'blocked')]} system={systemResponse()} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByRole('tab', { name: /Tailscale/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: 'Tailscale' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '0 installed · 0 running' })).toBeInTheDocument();
  });

  it('orders the app catalog with Productivity first', () => {
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    const groups = document.querySelector('.apps-sections');
    expect(Array.from(groups?.querySelectorAll(':scope > section > h2') || []).map(item => item.textContent))
      .toEqual(['Productivity apps', 'AI Integration', 'Foundation', 'Blocked and planned']);
  });

  it('keeps a planned Baby Buddy entry with productivity apps', () => {
    const babyBuddy = service('baby-buddy', 'Baby Buddy', 'blocked');
    babyBuddy.category = 'productivity';
    render(<AppsPanel services={[...services, babyBuddy]} catalog={{ ok: true, profiles: [], services: {} }} />);
    const card = screen.getByRole('button', { name: /Baby Buddy/ });
    expect(card.closest('.catalog-group')?.querySelector('h2')).toHaveTextContent('Productivity apps');
    expect(card).toHaveTextContent('BB');
  });

  it('searches the app catalog without losing its category context', () => {
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search app catalog' }), { target: { value: 'Nextcloud' } });
    expect(screen.getByRole('button', { name: /Nextcloud/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ollama/ })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Productivity apps' })).toBeInTheDocument();
  });

  it('keeps identity repair out of the Home launcher', () => {
    const identityServices = services.map(item => ({ ...item }));
    const nextcloud = identityServices.find(item => item.id === 'nextcloud')!;
    nextcloud.identity = { mode: 'native_oidc', state: 'unconfigured', launch_url: 'https://example:8453',
      detail: 'Native sign-in is not verified.', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<HomePanel services={identityServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('link', { name: 'Open app ↗' })).toHaveAttribute('href', 'https://example:8453');
    expect(screen.queryByRole('button', { name: /Repair.*sign-in/ })).not.toBeInTheDocument();
  });

  it('opens a verified legacy route while the identity projection is unavailable', () => {
    const legacyServices = services.map(item => ({ ...item }));
    const nextcloud = legacyServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    nextcloud.route_state = 'verified';
    nextcloud.ui = { state: 'ready', url: 'https://example:8453', label: 'Open securely', authentication: 'oidc', reason: '' };
    render(<HomePanel services={legacyServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('link', { name: 'Open app ↗' })).toHaveAttribute('href', 'https://example:8453');
  });

  it('keeps a healthy native-OIDC route launchable while owner migration is pending', () => {
    const pendingServices = services.map(item => ({ ...item }));
    const nextcloud = pendingServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    nextcloud.identity = { mode: 'native_oidc', state: 'unconfigured', launch_url: 'https://example:8453',
      detail: 'Owner migration is pending.', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<HomePanel services={pendingServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('link', { name: 'Open app ↗' })).toHaveAttribute('href', 'https://example:8453');
    expect(screen.queryByRole('button', { name: /Repair.*sign-in/ })).not.toBeInTheDocument();
  });

  it('keeps application sign-in repair on Connections', () => {
    const identityServices = services.map(item => ({ ...item }));
    const nextcloud = identityServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    nextcloud.identity = { mode: 'native_oidc', state: 'unconfigured', launch_url: 'https://example:8453',
      detail: 'Native sign-in is not verified.', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<AiMcpPanel services={identityServices} integrations={{ ok: true, policy: '', integrations: [] }} />);
    expect(screen.getByRole('heading', { name: 'Authentik connections' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Repair Nextcloud sign-in' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open app ↗' })).toHaveAttribute('href', 'https://example:8453');
  });

  it('separates automatically managed MCPs from manual setup', async () => {
    window.history.pushState({}, '', '/connections/mcp');
    const mcpServer = (overrides: Record<string, unknown>) => ({
      id: 'firecrawl-official', name: 'Firecrawl MCP', service_id: 'firecrawl', kind: 'official',
      transport: 'streamable-http', app_state: 'running', enabled: true, prepared: true,
      setup_mode: 'automatic', setup_detail: 'Mu3Lab manages this connection.', state: 'live',
      auth: { type: 'none', scopes: [], configured: true }, configuration: [], tools: [],
      review: { status: 'accepted', repository: 'https://example.test', revision: '1', preferred: true },
      ...overrides,
    });
    vi.stubGlobal('fetch', vi.fn(async (path: string) => ({
      ok: true,
      json: async () => path === '/api/v1/mcp/servers' ? {
        ok: true, policy: 'Application data only.', summary: { live: 1 },
        servers: [mcpServer({}), mcpServer({ id: 'mealie-community', name: 'Mealie MCP', service_id: 'mealie', enabled: false, prepared: false, setup_mode: 'manual', setup_detail: 'Add the application credential below.', state: 'authentication_required', auth: { type: 'service-credential', scopes: [], configured: false }, configuration: [{ key: 'api_token', type: 'secret', label: 'Mealie API token', required: true, secret_present: false }], review: { status: 'accepted', repository: 'https://example.test', revision: '1', preferred: true } })],
      } : {},
    })));
    render(<AiMcpPanel services={services} integrations={{ ok: true, policy: '', integrations: [] }} />);
    const automatic = await screen.findByRole('region', { name: 'Set up automatically' });
    const manual = screen.getByRole('region', { name: 'Manual integration' });
    expect(automatic).toHaveTextContent('Firecrawl MCP');
    expect(automatic).not.toHaveTextContent('Mealie MCP');
    expect(manual).toHaveTextContent('Mealie MCP');
    expect(screen.getByLabelText('MCP connection summary')).toHaveTextContent('1 managed automatically');
    window.history.pushState({}, '', '/connections');
  });

  it('makes Start the primary action for a stopped app without open or restart controls', () => {
    const stoppedServices = services.map(item => ({ ...item }));
    const nextcloud = stoppedServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'stopped';
    nextcloud.allowed_actions = ['start'];
    nextcloud.identity = { mode: 'native_oidc', state: 'ready', launch_url: 'https://example:8453', detail: '', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<HomePanel services={stoppedServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('button', { name: 'start' })).toHaveClass('button-primary');
    expect(screen.queryByRole('link', { name: /Open Nextcloud/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'restart' })).not.toBeInTheDocument();
  });

  it('uses concise launch controls and omits launch for internal services', () => {
    const launchServices = services.map(item => ({ ...item }));
    const caddy = launchServices.find(item => item.id === 'ingress')!;
    caddy.state = 'ready';
    caddy.identity = { mode: 'none', state: 'unsupported', launch_url: '', detail: '', last_verified_at: '', recovery_available: false, job_id: '' };
    caddy.ui = { state: 'unavailable', url: '', label: 'Open securely', authentication: 'none', reason: 'No dashboard' };
    const vaultwarden = service('vaultwarden', 'Vaultwarden', 'foundation');
    vaultwarden.state = 'ready';
    vaultwarden.identity = { mode: 'local', state: 'ready', launch_url: 'https://example:8444', detail: '', last_verified_at: '', recovery_available: true, job_id: '' };
    vaultwarden.ui = { state: 'ready', url: 'https://example:8444', label: '', authentication: 'local', reason: '' };
    launchServices.push(vaultwarden);
    render(<HomePanel services={launchServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Caddy/ }));
    expect(screen.queryByRole('link', { name: /Open/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Vaultwarden/ }));
    expect(screen.getByRole('link', { name: 'Login ↗' })).toHaveAttribute('href', 'https://example:8444');
    expect(screen.queryByText(/separate login/i)).not.toBeInTheDocument();
  });

  it('offers both FreeLLMAPI login and provider management', () => {
    const launchServices = services.map(item => ({ ...item }));
    const free = service('freellmapi', 'FreeLLMAPI', 'core');
    free.state = 'ready';
    free.identity = { mode: 'local', state: 'ready', launch_url: 'https://example:8455', detail: '', last_verified_at: '', recovery_available: true, job_id: '' };
    free.ui = { state: 'ready', url: 'https://example:8455', label: '', authentication: 'local', reason: '' };
    launchServices.push(free);
    render(<HomePanel services={launchServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /FreeLLMAPI/ }));
    expect(screen.getByRole('link', { name: 'Login ↗' })).toHaveAttribute('href', 'https://example:8455');
    expect(screen.getByRole('button', { name: 'Manage provider accounts' })).toBeInTheDocument();
  });

  it('places recovery credential export on Security and Backups', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => ({ ok: true, json: async () => path === '/api/v1/credential-handoffs' ? { handoffs: [{ id: 'handoff', service_id: 'nextcloud', job_id: 'job', state: 'available', created_at: '', expires_at: '2026-09-22T00:00:00Z', login_url: 'https://example:8453' }] } : {} })));
    render(<ProtectionPanel identity={{ ok: true, control_plane_auth: 'authentik', detail: 'Protected', writes_enabled: true }} backup={{ state: 'verified' }} audit={{ ok: true, available: true, events: [] }} />);
    expect(await screen.findByRole('button', { name: 'Export credentials' })).toBeInTheDocument();
    expect(screen.getByText(/Authentik SSO has no password to export/)).toBeInTheDocument();
  });

  it('uses the FullCalendar month view for connected calendar events', async () => {
    const calendarServices = services.map(item => ({ ...item }));
    const nextcloud = calendarServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(path.startsWith('/api/v1/calendar/events')
      ? { ok: true, json: async () => ({ ok: true, state: 'connected', calendar: { id: 'personal', name: 'Personal' }, events: [{ id: 'event-1', title: 'Planning', start: '2026-10-01T10:00:00Z', end: '2026-10-01T11:00:00Z', all_day: false, editable: true, revision: 'revision' }] }) }
      : { ok: true, json: async () => ({ handoffs: [] }) })));
    render(<HomePanel services={calendarServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    await waitFor(() => expect(document.querySelector('.calendar-widget .fc')).toBeInTheDocument());
    expect(screen.getByText('Planning')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /today/i })).toBeInTheDocument();
    expect(document.querySelector('.calendar-grid')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /New event/i }));
    expect(screen.getByRole('dialog', { name: 'Add to your calendar' })).toBeInTheDocument();
    expect(document.querySelector('.calendar-widget > .calendar-event-form')).not.toBeInTheDocument();
  });

  it('reconciles a completed reset when its response is lost', async () => {
    const batch = { id: 'batch', actor: 'operator', state: 'cancelled' as const, current_ordinal: 0,
      created_at: '', updated_at: '', items: [{ batch_id: 'batch', service_id: 'nextcloud', ordinal: 0,
        explicitly_selected: 1, state: 'failed', job_id: 'job', started_at: '', completed_at: '' }] };
    let batchReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/v1/service-install-batches' && (!init?.method || init.method === 'GET')) {
        batchReads += 1;
        return { ok: true, json: async () => batchReads === 1 ? { ok: true, batch, current_job: null } : { ok: true, batch: null, current_job: null } };
      }
      if (path === '/api/v1/session') return { ok: true, json: async () => ({ csrf_token: 'token' }) };
      if (path === '/api/v1/service-install-batches/batch/reset') throw new TypeError('Failed to fetch');
      return { ok: false, json: async () => ({ error: 'not found' }) };
    }));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    const reset = await screen.findByRole('button', { name: 'Reset failed installation' });
    fireEvent.click(reset);
    await waitFor(() => expect(screen.getByText('Installation reset completed. Select apps to try again.')).toBeInTheDocument());
    expect(screen.queryByText('INSTALLATION BATCH')).not.toBeInTheDocument();
  });
});
