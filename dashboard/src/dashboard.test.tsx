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
  it('opens a quick panel for each everyday app, with Open and its controls', () => {
    stubFetch(() => ({}));
    const immich = service('immich', 'Immich', 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8449/auth/login?autoLaunch=1' }),
      allowed_actions: ['stop', 'restart'],
    });
    const mealie = service('mealie', 'Mealie', 'optional', {
      display_state: 'stopped',
      identity: identity(),
      allowed_actions: ['start'],
    });
    const ollama = service('ollama', 'Ollama', 'core');
    renderWithDashboard(<HomePage />, dashboardData([immich, mealie, ollama]));
    const launcher = screen.getByRole('region', { name: 'Your apps' });
    expect(within(launcher).queryByText('Ollama')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Immich: Running' }));
    let panel = screen.getByRole('dialog', { name: 'Immich' });
    expect(within(panel).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      'https://host.ts.net:8449/auth/login?autoLaunch=1',
    );
    expect(within(panel).getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'All details' })).toHaveAttribute('href', '/apps/immich');
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));

    fireEvent.click(screen.getByRole('button', { name: 'Mealie: Stopped' }));
    panel = screen.getByRole('dialog', { name: 'Mealie' });
    expect(within(panel).queryByRole('link', { name: 'Open' })).not.toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'Start' })).toBeInTheDocument();
  });

  it('opens one System chip on donuts and a sortable list of every app, with core part by part', () => {
    stubFetch(() => ({}));
    const GB = 1024 ** 3;
    const data = dashboardData([
      service('authentik', 'Authentik', 'foundation'),
      service('ollama', 'Ollama', 'core'),
      service('immich', 'Immich', 'optional'),
    ]);
    data.system = {
      ...data.system,
      cpu_percent: 12,
      worker_state: 'failed',
      memory_by_owner: { ollama: 3.5 * GB, immich: 0.5 * GB, core: 1 * GB },
      memory_by_core_part: { authentik: 0.75 * GB, mu3lab: 0.25 * GB },
      disk_usage: {
        measured_at: new Date().toISOString(),
        owners: {
          ollama: { data: 20 * GB, program: 2 * GB },
          immich: { data: 48 * GB, program: 2 * GB },
          unused: { data: 0, program: 4 * GB },
        },
        removed_folders: [],
        core_parts: {},
      },
      memory: { total: 16 * GB, used: 10 * GB, percent: 62 },
      disk: { total: 100 * GB, used: 96 * GB, percent: 96 },
    };
    renderWithDashboard(<HomePage />, data);
    // The stopped worker is a Home warning, not a row in the panel.
    expect(screen.getByRole('link', { name: /background worker is failed/ })).toHaveAttribute(
      'href',
      '/settings/system',
    );
    const strip = screen.getByRole('region', { name: 'System status' });
    // Collapsed: one chip with the three figures, real sizes on hover.
    const chip = within(strip).getByRole('button');
    expect(chip).toHaveTextContent('SystemRAM 62% · Disk 96% · CPU 12%');
    expect(chip).toHaveAttribute('title', 'Memory 10.0 GB of 16.0 GB · Disk 96.0 GB of 100.0 GB');

    fireEvent.click(chip);
    for (const name of ['Memory: 10.0 GB of 16.0 GB used', 'Disk: 96.0 GB of 100.0 GB used', 'CPU: 12% of 100% used'])
      expect(within(strip).getByRole('img', { name })).toBeInTheDocument();
    const legend = within(strip).getByRole('table');
    const names = () =>
      within(legend)
        .getAllByRole('rowheader')
        .map((cell) => cell.textContent);
    const row = (name: string) =>
      within(legend).getByRole('rowheader', { name }).closest('tr')!.textContent?.replace(/\s+/g, ' ');
    // Biggest memory first; what can be freed, the rest and free always come last.
    expect(names()).toEqual(['Ollama', 'Mu3Lab core', 'Immich', 'Can be freed', 'Other (not Mu3Lab)', 'Free']);
    expect(row('Ollama')).toBe('Ollama3.5 GB22.0 GB');
    expect(within(legend).getByRole('link', { name: 'Ollama' })).toHaveAttribute('href', '/apps/ollama');
    expect(row('Can be freed')).toBe('Can be freed—4.0 GB');
    expect(row('Other (not Mu3Lab)')).toBe('Other (not Mu3Lab)5.0 GB20.0 GB');
    expect(row('Free')).toBe('Free6.0 GB4.0 GB');

    fireEvent.click(within(legend).getByRole('button', { name: 'Disk' }));
    expect(names().slice(0, 3)).toEqual(['Immich', 'Ollama', 'Mu3Lab core']);
    // The same header again: smallest first.
    fireEvent.click(within(legend).getByRole('button', { name: /Disk/ }));
    expect(names().slice(0, 3)).toEqual(['Mu3Lab core', 'Ollama', 'Immich']);

    // Core folds open to its parts, named after their apps.
    const core = within(legend).getByRole('button', { name: 'Mu3Lab core' });
    expect(core).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(core);
    expect(names()).toContain('Authentik');
    expect(names()).toContain('Mu3Lab dashboard and worker');
  });

  it('surfaces app problems in the attention strip', () => {
    renderWithDashboard(
      <HomePage />,
      dashboardData([service('paperless-ngx', 'Paperless-ngx', 'optional', { display_state: 'needs_attention' })]),
    );
    const strip = screen.getByRole('region', { name: 'Needs attention' });
    expect(within(strip).getByText('Paperless-ngx: needs attention')).toBeInTheDocument();
  });

  it('points to System settings when a setup step failed', () => {
    renderWithDashboard(
      <HomePage />,
      dashboardData([], {
        provisioning: { ok: true, available: true, complete: false, phases: [] },
        core: {
          ok: true,
          ready_to_run: true,
          services: [],
          missing_manifests: [],
          next_action: '',
          current_job: { state: 'failed' } as never,
        },
      }),
    );
    const strip = screen.getByRole('region', { name: 'Needs attention' });
    expect(
      within(strip).getByRole('link', { name: 'A setup step didn’t finish. Open System settings to try again.' }),
    ).toHaveAttribute('href', '/settings/system');
  });

  it('asks for attention when backups fall behind, linking to Backups', () => {
    renderWithDashboard(
      <HomePage />,
      dashboardData([], {
        backup_attention: [{ key: 'backup-overdue', message: "Mu3Lab's last backup was 9 days ago." }],
      }),
    );
    const strip = screen.getByRole('region', { name: 'Needs attention' });
    expect(within(strip).getByRole('link', { name: "Mu3Lab's last backup was 9 days ago." })).toHaveAttribute(
      'href',
      '/settings/security',
    );
  });
});

describe('App page', () => {
  it('makes Start the primary action for a stopped app without open or restart', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional', {
      display_state: 'stopped',
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

  it('links an app’s other private addresses beside its Open button', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const voice = service('voice', 'Voice', 'optional', {
      identity: identity({ mode: 'proxy_gate', launch_url: 'https://host.ts.net:8476' }),
      ui: readyUi('https://host.ts.net:8476'),
      links: [{ label: 'Admin dashboard', url: 'https://host.ts.net:8478' }],
    });
    renderWithDashboard(<AppDetailPage id="voice" tab="overview" />, dashboardData([voice]));
    expect(screen.getByRole('link', { name: 'Admin dashboard' })).toHaveAttribute('href', 'https://host.ts.net:8478');
    expect(screen.getByRole('link', { name: /^Open/ })).toHaveAttribute('href', 'https://host.ts.net:8476');
  });

  it('offers the GPU version after the compute mode changes, and switches only when asked', async () => {
    const fetchMock = stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '', job: { id: 'j' } }));
    const immich = service('immich', 'Immich', 'optional', { compute: { running: 'cpu', wanted: 'nvidia' } });
    renderWithDashboard(<AppDetailPage id="immich" tab="overview" />, dashboardData([immich]));
    expect(screen.getByText('An NVIDIA GPU version is available')).toBeInTheDocument();
    expect(screen.getByText('Runs on').nextSibling).toHaveTextContent('CPU');
    fireEvent.click(screen.getByRole('button', { name: 'Switch to GPU version' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/immich/actions',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'switch_compute' }) }),
      ),
    );
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

  it('points a failed address at Retry setup instead of the logs', async () => {
    const fetchMock = stubFetch(() => ({ ok: true, job: { id: 'j', state: 'queued' } }));
    const app = service('mealie', 'Mealie', 'optional', {
      display_state: 'needs_attention',
      installed: true,
      blocking_check: 'route',
      detail: "Mealie's private route did not answer.",
      recommended_action: 'retry_setup',
      allowed_actions: ['retry_setup', 'stop'],
    });
    renderWithDashboard(<AppDetailPage id="mealie" tab="overview" />, dashboardData([app]));
    const callout = screen.getByText(/sets up its address again; its data is kept/).closest('div')!.parentElement!;
    expect(within(callout).queryByRole('link', { name: 'View logs' })).not.toBeInTheDocument();
    fireEvent.click(within(callout).getByRole('button', { name: 'Retry setup' }));
    const dialog = await screen.findByRole('dialog', { name: 'Retry setup Mealie?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry setup' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/mealie/actions',
        expect.objectContaining({ body: JSON.stringify({ action: 'retry_setup' }) }),
      ),
    );
  });

  it('uninstalls keeping data by default and deletes data only after the name is typed', async () => {
    const fetchMock = stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '', job: { id: 'j' } }));
    const app = service('mealie', 'Mealie', 'optional', {
      allowed_actions: ['stop', 'restart', 'uninstall'],
      identity: identity({ launch_url: 'https://host.ts.net:8450' }),
    });
    renderWithDashboard(<AppDetailPage id="mealie" tab="overview" />, dashboardData([app]));
    const open = async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Mealie actions' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Uninstall…' }));
      return screen.findByRole('dialog', { name: 'Uninstall Mealie?' });
    };
    let dialog = await open();
    expect(within(dialog).getByRole('radio', { name: /Keep my data/ })).toBeChecked();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uninstall' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/mealie/actions',
        expect.objectContaining({ body: JSON.stringify({ action: 'uninstall', confirm: '' }) }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    dialog = await open();
    fireEvent.click(within(dialog).getByRole('radio', { name: /Delete everything/ }));
    const destroy = within(dialog).getByRole('button', { name: 'Uninstall and delete data' });
    expect(destroy).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('textbox', { name: /to confirm/ }), { target: { value: 'wrong app' } });
    expect(destroy).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('textbox', { name: /to confirm/ }), { target: { value: 'mEaLiE' } });
    expect(destroy).toBeEnabled();
    fireEvent.click(destroy);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/mealie/actions',
        expect.objectContaining({ body: JSON.stringify({ action: 'uninstall_delete_data', confirm: 'mEaLiE' }) }),
      ),
    );
  });

  it('helps connect companion apps with the server address right on the overview', () => {
    stubFetch(() => ({ ok: true, servers: [], summary: {}, policy: '' }));
    const immich = service('immich', 'Immich', 'optional', {
      identity: identity(),
      ui: readyUi('https://host.ts.net:8449'),
      mobile: {
        primary: 'immich-mobile',
        clients: [
          {
            id: 'immich-mobile',
            name: 'Immich',
            kind: 'native',
            support: 'official',
            platforms: ['android'],
            install: { android: 'https://play.google.com/store/apps/details?id=app.alextran.immich' },
            setup: 'server_url',
            summary: '',
            steps: ['Paste the server URL.'],
            homepage: '',
            source: '',
            caveat: '',
            fallback: false,
          },
        ],
      },
    });
    renderWithDashboard(<AppDetailPage id="immich" tab="overview" />, dashboardData([immich]));
    expect(screen.queryByRole('link', { name: 'Devices' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Apps' })).toHaveAttribute('href', '/apps');
    // Shown once, in the server address card, rather than again under About.
    expect(screen.getAllByText('https://host.ts.net:8449')).toHaveLength(1);
    expect(screen.getByRole('link', { name: /Google Play/ })).toHaveAttribute(
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
    const available = {
      display_state: 'not_installed' as const,
      installed: false,
      allowed_actions: ['install'] as Array<'install'>,
    };
    renderWithDashboard(
      <AppsPage discover />,
      dashboardData([
        service('mealie', 'Mealie', 'optional', available),
        service('immich', 'Immich', 'optional', available),
      ]),
    );
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
          // Two apps never download more than two at once.
          body: JSON.stringify({ service_ids: ['immich', 'mealie'], parallel_downloads: 2 }),
        }),
      ),
    );
  });

  it('disables queued app cards while allowing new selections to join the queue', async () => {
    const batch = {
      id: 'b',
      state: 'running',
      current_ordinal: 0,
      items: [
        {
          service_id: 'mealie',
          ordinal: 0,
          state: 'pending',
          download_state: 'downloading',
          explicitly_selected: true,
        },
      ],
    };
    const fetchMock = stubFetch((path) =>
      path === '/api/v1/services/install-batch'
        ? {
            ok: true,
            batch: {
              ...batch,
              items: [
                ...batch.items,
                { service_id: 'immich', ordinal: 1, state: 'pending', download_state: '', explicitly_selected: true },
              ],
            },
          }
        : { ok: true, batch },
    );
    const available = {
      display_state: 'not_installed' as const,
      installed: false,
      allowed_actions: ['install'] as Array<'install'>,
    };
    renderWithDashboard(
      <AppsPage discover />,
      dashboardData([
        service('mealie', 'Mealie', 'optional', available),
        service('immich', 'Immich', 'optional', available),
      ]),
    );
    expect(await screen.findByRole('button', { name: 'Downloading' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Select Mealie for installation' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Downloads at once')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Select Immich for installation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Install Immich' }));
    // One app has nothing to download alongside it.
    expect(screen.queryByLabelText('Downloads at once')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Install all selected' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/install-batch',
        expect.objectContaining({ body: JSON.stringify({ service_ids: ['immich'], parallel_downloads: 1 }) }),
      ),
    );
    expect(await screen.findByRole('button', { name: 'Queued' })).toBeDisabled();
  });

  it('selects an app by clicking anywhere on its card except its name', () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    stubFetch(() => ({ ok: true, batch: null }));
    const available = {
      display_state: 'not_installed' as const,
      installed: false,
      allowed_actions: ['install'] as Array<'install'>,
    };
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
    const available = {
      display_state: 'not_installed' as const,
      installed: false,
      allowed_actions: ['install'] as Array<'install'>,
    };
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
    const available = {
      display_state: 'not_installed' as const,
      installed: false,
      allowed_actions: ['install'] as Array<'install'>,
    };
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
      auth: { type: 'none', configured: true, auto_provision: false },
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
                auth: { type: 'service-credential', configured: false, auto_provision: false },
              }),
            ],
          }
        : undefined,
    );
    const apps = [
      service('actual-budget', 'Actual Budget', 'optional', { group: 'apps' }),
      service('navidrome', 'Navidrome', 'optional', { group: 'apps' }),
      service('romm', 'RomM', 'optional', { group: 'apps', installed: false, display_state: 'not_installed' }),
    ];
    renderWithDashboard(<IntegrationsSettings />, dashboardData(apps));
    const automatic = await screen.findByRole('region', { name: 'Set up automatically' });
    // Every installed app is accounted for, including those with no integration yet.
    const without = screen.getByRole('region', { name: 'Apps without an integration' });
    expect(without).toHaveTextContent('Navidrome');
    expect(without).not.toHaveTextContent('Actual Budget');
    expect(without).not.toHaveTextContent('RomM');
    const manual = screen.getByRole('region', { name: 'Manual integration' });
    expect(automatic).toHaveTextContent('Firecrawl MCP');
    expect(manual).toHaveTextContent('Actual Budget MCP');
    expect(within(manual).getByRole('link', { name: 'Add credential' })).toHaveAttribute(
      'href',
      '/apps/actual-budget/chat',
    );
  });

  it('shows access and backups without temporary credential handoffs', () => {
    stubFetch(() => undefined);
    const kopia = service('kopia-ui', 'Kopia backups', 'optional', { group: 'infrastructure' });
    renderWithDashboard(<SecuritySettings />, dashboardData([kopia]));
    expect(screen.getByText('Your access')).toBeInTheDocument();
    expect(screen.getByText('Backup protection is not verified')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Export credentials' })).not.toBeInTheDocument();
  });

  it('says backups are off until Kopia backups is installed', () => {
    const kopia = service('kopia-ui', 'Kopia backups', 'optional', {
      group: 'infrastructure',
      installed: false,
      display_state: 'not_installed',
    });
    renderWithDashboard(<SecuritySettings />, dashboardData([kopia]));
    // One folded card: the reason and the way to install it; the schedule waits inside.
    const toggle = screen.getByRole('button', { name: /Backups are off: Kopia backups isn’t installed/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('link', { name: 'Install Kopia backups' })).toHaveAttribute('href', '/apps/kopia-ui');
    expect(screen.queryByText('Schedule')).not.toBeInTheDocument();
    expect(screen.queryByText('Mu3Lab itself')).not.toBeInTheDocument();
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

// Mid-morning today, so a test event never runs past midnight and shows on two days.
const todayAt10 = () => {
  const date = new Date();
  date.setHours(10, 0, 0, 0);
  return date;
};

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
                start: todayAt10().toISOString(),
                end: todayAt10().toISOString(),
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
  it('opens calendar settings from the Calendar page, not Settings', async () => {
    stubFetch((path) =>
      path.startsWith('/api/v1/calendar/events')
        ? { ok: true, state: 'local', calendar: { id: 'local', name: 'Calendar' }, events: [] }
        : { ok: true, state: 'not_connected', calendars: [], selected_calendar_id: '' },
    );
    renderWithDashboard(<CalendarPage />, dashboardData([service('nextcloud', 'Nextcloud', 'optional')]));
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Nextcloud' }));
    const panel = screen.getByRole('dialog', { name: 'Calendar settings' });
    expect(within(panel).getByText('Nextcloud calendar')).toBeInTheDocument();
  });
  it('works as a local calendar before Nextcloud is installed', async () => {
    const fetchMock = stubFetch((path) =>
      path.startsWith('/api/v1/calendar/events')
        ? { ok: true, state: 'local', calendar: { id: 'local', name: 'Calendar' }, events: [] }
        : { ok: true, id: 'local-1' },
    );
    renderWithDashboard(<CalendarPage />, dashboardData([]));
    expect(await screen.findByText(/Saved on Mu3Lab/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Install Nextcloud' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /New event/ }));
    const dialog = screen.getByRole('dialog', { name: 'New event' });
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Dentist' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add event' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/calendar/events',
        expect.objectContaining({ method: 'POST', body: expect.stringContaining('Dentist') }),
      ),
    );
    expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/calendar/auto-connect', expect.anything());
  });
});

describe('Calendar sync', () => {
  const event = (overrides = {}) => ({
    id: 'local-1',
    title: 'Piano',
    start: todayAt10().toISOString(),
    end: new Date(todayAt10().getTime() + 3600000).toISOString(),
    all_day: false,
    editable: true,
    revision: 'r',
    local: true,
    ...overrides,
  });

  it('sends waiting events with an explicit sync, never by reading', async () => {
    let pending = true;
    const fetchMock = stubFetch((path, init) => {
      if (path === '/api/v1/calendar/sync' && init?.method === 'POST') {
        pending = false;
        return { ok: true, moved: 1, conflicts: [], kept: [] };
      }
      if (path.startsWith('/api/v1/calendar/events'))
        return { ok: true, state: 'connected', pending_upload: pending, events: pending ? [event()] : [] };
      return undefined;
    });
    renderWithDashboard(<CalendarPage />, dashboardData([service('nextcloud', 'Nextcloud', 'optional')]));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/calendar/sync', expect.objectContaining({ method: 'POST' })),
    );
    const reads = fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/v1/calendar/events'));
    expect(reads.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
    await waitFor(() => expect(reads.length).toBeGreaterThanOrEqual(1));
  });

  it('offers both versions of an event that also changed in Nextcloud', async () => {
    const fetchMock = stubFetch((path) => {
      if (path.endsWith('/resolve')) return { ok: true };
      if (path.startsWith('/api/v1/calendar/events'))
        return {
          ok: true,
          state: 'connected',
          events: [event({ conflict: 'This event was also changed in Nextcloud. Choose which version to keep.' })],
        };
      return undefined;
    });
    renderWithDashboard(<CalendarPage />, dashboardData([service('nextcloud', 'Nextcloud', 'optional')]));
    fireEvent.click(await screen.findByText('⚠ Piano'));
    const dialog = screen.getByRole('dialog', { name: 'Edit event' });
    expect(within(dialog).getByText('Two versions of this event')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/calendar/events/local-1/resolve',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ keep: 'mine' }) }),
      ),
    );
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
        : path.startsWith('/api/v1/snapshot')
          ? dashboardData([service('immich', 'Immich', 'optional', { identity: identity() })])
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
