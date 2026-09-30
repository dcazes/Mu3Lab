import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { AppDetailPage } from './features/apps/AppDetailPage';
import { AppsPage } from './features/apps/AppsPage';
import { CalendarPage } from './features/calendar/CalendarPage';
import { HomePage } from './features/home/HomePage';
import { IntegrationsSettings } from './features/settings/IntegrationsSettings';
import { SecuritySettings } from './features/settings/SecuritySettings';
import { SystemSettings } from './features/settings/SystemSettings';
import { resolvePath } from './lib/router';
import { dashboardData, identity, readyUi, renderWithDashboard, service, stubFetch } from './test/fixtures';

beforeEach(() => {
  window.history.pushState({}, '', '/');
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Home', () => {
  it('launches everyday apps and routes stopped ones to their page', () => {
    stubFetch(() => ({ handoffs: [] }));
    const immich = service('immich', 'Immich', 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8449' }),
    });
    const mealie = service('mealie', 'Mealie', 'optional', { state: 'stopped', identity: identity() });
    const ollama = service('ollama', 'Ollama', 'core');
    renderWithDashboard(<HomePage />, dashboardData([immich, mealie, ollama]));
    expect(screen.getByRole('link', { name: 'Open Immich' })).toHaveAttribute(
      'href',
      'https://host.ts.net:8449/auth/login?autoLaunch=1',
    );
    expect(screen.getByRole('link', { name: 'Mealie: Stopped' })).toHaveAttribute('href', '/apps/mealie');
    const launcher = screen.getByRole('region', { name: 'Your apps' });
    expect(within(launcher).queryByText('Ollama')).not.toBeInTheDocument();
  });

  it('groups status into Security, AI and System chips that expand with real usage figures', () => {
    stubFetch(() => ({ handoffs: [] }));
    const GB = 1024 ** 3;
    const data = dashboardData([
      service('ingress', 'Caddy', 'foundation'),
      service('authentik', 'Authentik', 'foundation'),
      service('ollama', 'Ollama', 'core', {
        state: 'failed',
        detail: 'Container exited.',
        containers: [
          { service: 'ollama', name: 'mu3lab-ollama', state: 'running', status: 'Up', health: 'unknown', image: 'x' },
        ],
      }),
      service('firecrawl', 'Firecrawl', 'optional', { state: 'not_installed' }),
    ]);
    data.system = {
      ...data.system,
      worker_state: 'failed',
      container_memory: { 'mu3lab-ollama': 3.5 * GB },
      memory: { total: 16 * GB, used: 10 * GB, percent: 62 },
      disk: { total: 100 * GB, used: 96 * GB, percent: 96 },
      tailscale: {
        state: 'connected',
        backend_state: 'Running',
        online: true,
        dns_name: 'host.ts.net',
        detail: '',
        serve: { state: 'available', ports: [] },
      },
    };
    renderWithDashboard(<HomePage />, data);
    const strip = screen.getByRole('region', { name: 'System status' });
    // Collapsed: three chips, no detail rows, real figures on hover.
    const chips = within(strip).getAllByRole('button');
    expect(chips.map((chip) => chip.textContent)).toEqual([
      'Security3/3 healthy',
      'AI1 needs attention',
      'SystemRAM 62% · Disk 96%',
    ]);
    expect(within(strip).queryByRole('link')).not.toBeInTheDocument();
    expect(chips[2]).toHaveAttribute('title', 'Memory 10.0 GB of 16.0 GB · Disk 96.0 GB of 100.0 GB');

    fireEvent.click(chips[0]);
    const caddy = within(strip).getByRole('link', { name: 'Caddy: Running' });
    expect(caddy).toHaveAttribute('href', '/apps/ingress');
    // Healthy rows leave the state to the dot.
    expect(caddy).not.toHaveTextContent('Running');
    expect(within(strip).getByRole('link', { name: 'Tailscale: Connected' })).toBeInTheDocument();

    fireEvent.click(chips[1]);
    expect(chips[0]).toHaveAttribute('aria-expanded', 'false');
    const ollama = within(strip).getByRole('link', { name: 'Ollama: Failed' });
    expect(ollama).toHaveAttribute('title', '3.5 GB — Container exited.');
    expect(ollama).toHaveTextContent('Failed');
    expect(within(strip).getByRole('link', { name: 'Firecrawl: Not installed' })).toBeInTheDocument();

    fireEvent.click(chips[2]);
    expect(within(strip).getByRole('link', { name: 'Background worker: Failed' })).toBeInTheDocument();
    expect(within(strip).getByRole('link', { name: 'Docker: Running' })).toBeInTheDocument();
    const memory = within(strip).getByRole('link', { name: 'Memory: 62%' });
    expect(memory).toHaveAttribute('title', '10.0 GB of 16.0 GB');
    expect(memory).toHaveTextContent('Memory62% of 16.0 GB');
    expect(within(strip).getByRole('link', { name: 'Disk: 96%' })).toBeInTheDocument();
  });

  it('surfaces problems and saved-password reminders in one strip', async () => {
    stubFetch((path) =>
      path === '/api/v1/credential-handoffs'
        ? {
            handoffs: [
              {
                id: 'h',
                service_id: 'nextcloud',
                job_id: 'j',
                state: 'available',
                created_at: '',
                expires_at: '',
                login_url: '',
              },
            ],
          }
        : undefined,
    );
    renderWithDashboard(
      <HomePage />,
      dashboardData([service('paperless-ngx', 'Paperless-ngx', 'optional', { state: 'failed' })]),
    );
    const strip = screen.getByRole('region', { name: 'Needs attention' });
    expect(within(strip).getByText('Paperless-ngx: failed')).toBeInTheDocument();
    expect(await within(strip).findByText('1 new app password to save in Vaultwarden')).toBeInTheDocument();
  });
});

describe('App page', () => {
  it('makes Start the primary action for a stopped app without open or restart', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional', {
      state: 'stopped',
      allowed_actions: ['start'],
      identity: identity(),
    });
    renderWithDashboard(<AppDetailPage id="nextcloud" tab="overview" />, dashboardData([nextcloud]));
    expect(screen.getByRole('button', { name: 'Start' })).toHaveClass('btn-primary');
    expect(screen.queryByRole('link', { name: /Open/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nextcloud actions' })).not.toBeInTheDocument();
  });

  it('opens the Authentik-gated FreeLLMAPI dashboard alongside provider management', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const free = service('freellmapi', 'FreeLLMAPI', 'core', {
      identity: identity({ mode: 'proxy_gate', launch_url: 'https://host.ts.net:8455' }),
      ui: readyUi('https://host.ts.net:8455'),
    });
    renderWithDashboard(<AppDetailPage id="freellmapi" tab="overview" />, dashboardData([free]));
    expect(screen.getByRole('link', { name: /^Open/ })).toHaveAttribute('href', 'https://host.ts.net:8455');
    expect(screen.getByRole('link', { name: 'Manage providers' })).toHaveAttribute('href', '/settings/ai');
  });

  it('asks for confirmation before stopping an app', async () => {
    const fetchMock = stubFetch(() => ({
      ok: true,
      servers: [],
      summary: {},
      policy: '',
      job: { id: 'j', state: 'queued' },
    }));
    const app = service('mealie', 'Mealie', 'optional', {
      allowed_actions: ['stop', 'restart'],
      identity: identity({ launch_url: 'https://host.ts.net:8450' }),
    });
    renderWithDashboard(<AppDetailPage id="mealie" tab="overview" />, dashboardData([app]));
    fireEvent.click(screen.getByRole('button', { name: 'Mealie actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Stop' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop Mealie?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stop' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/mealie/actions',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'stop' }) }),
      ),
    );
  });

  it('helps connect companion apps with the server address right on the overview', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const immich = service('immich', 'Immich', 'optional', {
      identity: identity(),
      ui: readyUi('https://host.ts.net:8449'),
    });
    renderWithDashboard(<AppDetailPage id="immich" tab="overview" />, dashboardData([immich]));
    expect(screen.queryByRole('link', { name: 'Devices' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Apps' })).toHaveAttribute('href', '/apps');
    // Shown once, in the server address card, rather than again under About.
    expect(screen.getAllByText('https://host.ts.net:8449')).toHaveLength(1);
    expect(screen.getByRole('link', { name: /Android/ })).toHaveAttribute(
      'href',
      'https://play.google.com/store/apps/details?id=app.alextran.immich',
    );
  });
});

describe('Apps', () => {
  it('reconciles a completed reset when its response is lost', async () => {
    const batch = {
      id: 'batch',
      actor: 'operator',
      state: 'cancelled',
      current_ordinal: 0,
      created_at: '',
      updated_at: '',
      items: [
        {
          batch_id: 'batch',
          service_id: 'nextcloud',
          ordinal: 0,
          explicitly_selected: 1,
          state: 'failed',
          job_id: 'job',
          started_at: '',
          completed_at: '',
        },
      ],
    };
    let reads = 0;
    stubFetch((path) => {
      if (path === '/api/v1/service-install-batches') {
        reads += 1;
        return reads === 1 ? { ok: true, batch, current_job: null } : { ok: true, batch: null, current_job: null };
      }
      if (path === '/api/v1/service-install-batches/batch/reset') return new TypeError('Failed to fetch');
      return undefined;
    });
    renderWithDashboard(<AppsPage discover={false} />, dashboardData([service('nextcloud', 'Nextcloud', 'optional')]));
    fireEvent.click(await screen.findByRole('button', { name: 'Reset failed installation' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reset' }));
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Installation progress' })).not.toBeInTheDocument(),
    );
    expect(reads).toBe(2);
  });

  it('installs several selected apps in one batch, smallest download first', async () => {
    const size = (needed: number) => ({
      download_bytes: needed,
      needed_bytes: needed,
      disk_bytes: needed * 4,
      needed_disk_bytes: needed * 4,
      updated_at: '',
      complete: true,
    });
    const fetchMock = stubFetch((path) =>
      path === '/api/v1/services/install-batch'
        ? { ok: true, batch: { id: 'b', state: 'queued', current_ordinal: 0, items: [] } }
        : path.startsWith('/api/v1/app-sizes')
          ? {
              ok: true,
              apps: { mealie: size(400 * 1024 ** 2), immich: size(100 * 1024 ** 2) },
              selection: size(500 * 1024 ** 2),
              measured: true,
              free_bytes: 100 * 1024 ** 3,
            }
          : { ok: true, batch: null },
    );
    const available = { state: 'not_installed' as const, installation_state: 'not_installed' as const };
    renderWithDashboard(
      <AppsPage discover />,
      dashboardData([
        service('mealie', 'Mealie', 'optional', available),
        service('immich', 'Immich', 'optional', available),
        service('planned', 'Planned', 'blocked', { state: 'blocked', blocked_reason: 'Under review' }),
      ]),
    );
    expect(screen.getByText('Under review')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Select Mealie for installation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Select Immich for installation' }));
    expect(await screen.findByText('400.0 MB download · about 1.6 GB on disk')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Install 2 apps' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getAllByRole('listitem').map((item) => item.querySelector('b')?.textContent)).toEqual([
      'Immich',
      'Mealie',
    ]);
    fireEvent.click(dialog.getByRole('button', { name: 'Install all selected' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/install-batch',
        expect.objectContaining({
          body: JSON.stringify({ service_ids: ['immich', 'mealie'], parallel_downloads: 3 }),
        }),
      ),
    );
  });

  it('selects an app by clicking anywhere on its card except its name', () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    stubFetch(() => ({ ok: true, batch: null }));
    const available = { state: 'not_installed' as const, installation_state: 'not_installed' as const };
    renderWithDashboard(<AppsPage discover />, dashboardData([service('mealie', 'Mealie', 'optional', available)]));
    const card = screen.getByRole('article');
    fireEvent.click(card);
    expect(screen.getByRole('button', { name: 'Deselect Mealie for installation' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // The button toggles once, not once for itself and again for the card.
    fireEvent.click(screen.getByRole('button', { name: 'Deselect Mealie for installation' }));
    expect(screen.getByRole('button', { name: 'Select Mealie for installation' })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('link', { name: /Mealie/ }));
    expect(window.location.pathname).toBe('/apps/mealie');
    expect(screen.queryByRole('region', { name: 'Selected apps' })).not.toBeInTheDocument();
  });

  it('keeps the selection after visiting an app page and coming back', () => {
    stubFetch(() => ({ ok: true, batch: null }));
    const available = { state: 'not_installed' as const, installation_state: 'not_installed' as const };
    const data = dashboardData([
      service('mealie', 'Mealie', 'optional', available),
      service('immich', 'Immich', 'optional', available),
    ]);
    renderWithDashboard(<AppsPage discover />, data);
    fireEvent.click(screen.getByRole('button', { name: 'Select Mealie for installation' }));
    cleanup();
    renderWithDashboard(<AppsPage discover />, data);
    expect(screen.getByRole('button', { name: 'Deselect Mealie for installation' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Selected apps' })).toHaveTextContent('1 selected · Mealie');
  });

  it('will not start an install that cannot fit on the disk', async () => {
    const big = { download_bytes: 10, needed_bytes: 10, disk_bytes: 50 * 1024 ** 3, needed_disk_bytes: 50 * 1024 ** 3 };
    stubFetch((path) =>
      path.startsWith('/api/v1/app-sizes')
        ? {
            ok: true,
            apps: { mealie: { ...big, updated_at: '', complete: true } },
            selection: big,
            measured: true,
            free_bytes: 10 * 1024 ** 3,
          }
        : { ok: true, batch: null },
    );
    const available = { state: 'not_installed' as const, installation_state: 'not_installed' as const };
    renderWithDashboard(<AppsPage discover />, dashboardData([service('mealie', 'Mealie', 'optional', available)]));
    fireEvent.click(screen.getByRole('button', { name: 'Select Mealie for installation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Install Mealie' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText('Not enough free disk space')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Install all selected' })).toBeDisabled();
  });
});

describe('Settings', () => {
  it('separates automatic chat integrations from ones needing a credential', async () => {
    const server = (overrides: Record<string, unknown>) => ({
      id: 'firecrawl-official',
      name: 'Firecrawl MCP',
      service_id: 'firecrawl',
      kind: 'official',
      transport: 'streamable-http',
      app_state: 'running',
      enabled: true,
      state: 'live',
      auth: { type: 'none', scopes: [], configured: true, auto_provision: false },
      configuration: [],
      tools: [],
      review: { status: 'accepted', repository: '', revision: '1', preferred: true },
      ...overrides,
    });
    stubFetch((path) =>
      path === '/api/v1/mcp/servers'
        ? {
            ok: true,
            policy: '',
            summary: {},
            servers: [
              server({}),
              server({
                id: 'actual',
                name: 'Actual Budget MCP',
                service_id: 'actual-budget',
                state: 'authentication_required',
                enabled: false,
                auth: { type: 'service-credential', scopes: [], configured: false, auto_provision: false },
              }),
            ],
          }
        : undefined,
    );
    renderWithDashboard(<IntegrationsSettings />, dashboardData([]));
    const automatic = await screen.findByRole('region', { name: 'Set up automatically' });
    const manual = screen.getByRole('region', { name: 'Manual integration' });
    expect(automatic).toHaveTextContent('Firecrawl MCP');
    expect(manual).toHaveTextContent('Actual Budget MCP');
    expect(within(manual).getByRole('link', { name: 'Add credential' })).toHaveAttribute(
      'href',
      '/apps/actual-budget/chat',
    );
  });

  it('offers recovery credential export on Security', async () => {
    stubFetch((path) =>
      path === '/api/v1/credential-handoffs'
        ? {
            handoffs: [
              {
                id: 'h',
                service_id: 'nextcloud',
                job_id: 'j',
                state: 'available',
                created_at: '',
                expires_at: '2030-01-01T00:00:00Z',
                login_url: '',
              },
            ],
          }
        : undefined,
    );
    renderWithDashboard(<SecuritySettings />, dashboardData([]));
    expect(await screen.findByRole('button', { name: 'Export credentials' })).toBeInTheDocument();
    expect(screen.getByText(/Handoff expiry removes access to the saved copy/)).toBeInTheDocument();
  });

  it('copies exactly the tailnet address', async () => {
    stubFetch(() => ({
      ok: true,
      compute_mode: 'auto',
      resolved_compute_mode: 'cpu',
      available_modes: ['auto', 'cpu'],
    }));
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    renderWithDashboard(
      <SystemSettings />,
      dashboardData([], {
        system: {
          ...dashboardData([]).system,
          tailscale: {
            state: 'connected',
            backend_state: 'Running',
            online: true,
            dns_name: 'mu3lab.tail.ts.net',
            detail: '',
            serve: { state: 'available', ports: [443, 8443] },
          },
        },
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy tailnet address' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('mu3lab.tail.ts.net'));
    expect(screen.getByText('2 private HTTPS routes')).toBeInTheDocument();
  });
});

describe('Calendar', () => {
  it('shows the month view and opens the event editor', async () => {
    stubFetch((path) =>
      path.startsWith('/api/v1/calendar/events')
        ? {
            ok: true,
            state: 'connected',
            calendar: { id: 'personal', name: 'Personal' },
            events: [
              {
                id: 'e1',
                title: 'Planning',
                start: new Date().toISOString(),
                end: new Date().toISOString(),
                all_day: false,
                editable: true,
                revision: 'r',
              },
            ],
          }
        : undefined,
    );
    renderWithDashboard(<CalendarPage />, dashboardData([service('nextcloud', 'Nextcloud', 'optional')]));
    expect(await screen.findByText('Planning')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Personal' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /New event/ }));
    expect(screen.getByRole('dialog', { name: 'New event' })).toBeInTheDocument();
  });
});

describe('Shell', () => {
  it('shows a clear offline screen when Mu3Lab is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))),
    );
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Can’t reach Mu3Lab' })).toBeInTheDocument();
  });

  it('asks to sign in again when Authentik redirects the API', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ type: 'opaqueredirect', status: 0, ok: false }) as Response),
    );
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Your session ended' })).toBeInTheDocument();
  });

  it('opens the command menu with Ctrl+K and switches theme', async () => {
    stubFetch((path) =>
      path === '/api/health'
        ? { ok: true }
        : path === '/api/v1/services'
          ? {
              ok: true,
              tailnet_dns_name: '',
              services: [service('immich', 'Immich', 'optional', { identity: identity() })],
            }
          : {},
    );
    render(<App />);
    await screen.findByRole('heading', { name: /Good/ });
    act(() => {
      fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    });
    const menu = await screen.findByRole('dialog');
    expect(within(menu).getByText('Immich settings and status')).toBeInTheDocument();
    fireEvent.click(within(menu).getByText('Dark theme'));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('redirects old dashboard paths to their new homes', () => {
    expect(resolvePath('/connections/providers')).toBe('/settings/ai');
    expect(resolvePath('/connections/mcp')).toBe('/settings/integrations');
    expect(resolvePath('/system/')).toBe('/settings/system');
    expect(resolvePath('/apps/immich')).toBe('/apps/immich');
  });
});
