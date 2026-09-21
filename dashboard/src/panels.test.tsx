import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppsPanel, HomePanel } from './panels';
import type { Service } from './api';

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

const services = [service('ingress', 'Caddy', 'foundation'), service('ollama', 'Ollama', 'core'), service('nextcloud', 'Nextcloud', 'optional'), service('firecrawl', 'Firecrawl', 'blocked')];

describe('dashboard organization', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ state: 'not_connected', events: [], error: '' }) })); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('groups Home apps and removes the duplicate wiring panel', () => {
    render(<HomePanel services={services} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    expect(screen.getByRole('heading', { name: 'Infrastructure' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'AI Integration' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Productivity apps' })).toBeInTheDocument();
    expect(screen.queryByText('CORE WIRING')).not.toBeInTheDocument();
    expect(screen.queryByText('6/9')).not.toBeInTheDocument();
  });

  it('orders the app catalog with Productivity first', () => {
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    const groups = document.querySelector('.apps-sections');
    expect(Array.from(groups?.querySelectorAll(':scope > section > h2') || []).map(item => item.textContent))
      .toEqual(['Productivity apps', 'AI Integration', 'Foundation', 'Blocked and planned']);
  });

  it('searches the app catalog without losing its category context', () => {
    render(<AppsPanel services={services} catalog={{ ok: true, profiles: [], services: {} }} />);
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search app catalog' }), { target: { value: 'Nextcloud' } });
    expect(screen.getByRole('button', { name: /Nextcloud/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ollama/ })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Productivity apps' })).toBeInTheDocument();
  });

  it('does not present unverified native OIDC as a working launch', () => {
    const identityServices = services.map(item => ({ ...item }));
    const nextcloud = identityServices.find(item => item.id === 'nextcloud')!;
    nextcloud.identity = { mode: 'native_oidc', state: 'unconfigured', launch_url: 'https://example:8453',
      detail: 'Native sign-in is not verified.', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<HomePanel services={identityServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('button', { name: 'Repair sign-in' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open with Authentik/ })).not.toBeInTheDocument();
  });

  it('opens a verified legacy route while the identity projection is unavailable', () => {
    const legacyServices = services.map(item => ({ ...item }));
    const nextcloud = legacyServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    nextcloud.route_state = 'verified';
    nextcloud.ui = { state: 'ready', url: 'https://example:8453', label: 'Open securely', authentication: 'oidc', reason: '' };
    render(<HomePanel services={legacyServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('link', { name: 'Open securely ↗' })).toHaveAttribute('href', 'https://example:8453');
  });

  it('keeps a healthy native-OIDC route launchable while owner migration is pending', () => {
    const pendingServices = services.map(item => ({ ...item }));
    const nextcloud = pendingServices.find(item => item.id === 'nextcloud')!;
    nextcloud.state = 'ready';
    nextcloud.identity = { mode: 'native_oidc', state: 'unconfigured', launch_url: 'https://example:8453',
      detail: 'Owner migration is pending.', last_verified_at: '', recovery_available: true, job_id: '' };
    render(<HomePanel services={pendingServices} system={{ ok: true, cpu_percent: 1, uptime_seconds: 1, docker_ready: true, tailnet_dns_name: '', runtime_root: '', memory: { total: 1, used: 1, percent: 1 }, disk: { total: 1, used: 1, percent: 1 }, backup: {} }} jobs={{ ok: true, available: true, jobs: [] }} />);
    fireEvent.click(screen.getByRole('tab', { name: /Nextcloud/ }));
    expect(screen.getByRole('link', { name: 'Open sign-in ↗' })).toHaveAttribute('href', 'https://example:8453');
    expect(screen.getByRole('button', { name: 'Repair sign-in' })).toBeInTheDocument();
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
