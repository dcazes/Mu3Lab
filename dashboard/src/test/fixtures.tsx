import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Service, ServiceIdentity } from '../api';
import { ConfirmProvider } from '../components/Dialog';
import { type DashboardData, DashboardProvider, emptyData } from '../state/dashboard';

export function service(id: string, name: string, stage: Service['stage'], overrides: Partial<Service> = {}): Service {
  return {
    id,
    name,
    stage,
    category: stage === 'optional' ? 'productivity' : 'infrastructure',
    lifecycle: 'optional',
    https_port: 1,
    maturity: stage === 'blocked' ? 'planned' : 'supported',
    auth: 'oidc',
    profiles: [],
    dependencies: [],
    availability: stage === 'blocked' ? 'blocked' : 'available',
    blocked_reason: '',
    route: 'ready',
    routable: true,
    required: stage !== 'optional',
    identity_note: '',
    resource_guidance: '',
    setup_action: '',
    mcp: { exposed: false, risk: '' },
    state: 'ready',
    lifecycle_state: 'ready',
    health_state: 'healthy',
    setup_state: 'configured',
    route_state: 'verified',
    identity_mode: '',
    backup_state: '',
    last_job_id: '',
    last_error: '',
    user_action: '',
    detail: '',
    url: `https://host.ts.net:${id.length + 8440}`,
    route_ready: true,
    compose_present: true,
    installation_state: 'installed',
    ...overrides,
  };
}

export function identity(overrides: Partial<ServiceIdentity> = {}): ServiceIdentity {
  return {
    mode: 'native_oidc',
    state: 'ready',
    launch_url: 'https://host.ts.net:8453',
    detail: '',
    last_verified_at: '',
    recovery_available: true,
    job_id: '',
    ...overrides,
  };
}

export const readyUi = (url: string): Service['ui'] => ({
  state: 'ready',
  url,
  label: 'Open',
  authentication: 'oidc',
  reason: '',
});

export function dashboardData(services: Service[], overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    ...emptyData,
    services: { ok: true, tailnet_dns_name: 'host.ts.net', services },
    identity: {
      ok: true,
      control_plane_auth: 'authentik_forward_auth',
      username: 'owner',
      display_name: 'Owner',
      detail: '',
      writes_enabled: true,
    },
    system: { ...emptyData.system, ok: true, docker_ready: true, tailnet_dns_name: 'host.ts.net' },
    ...overrides,
  };
}

export function renderWithDashboard(ui: ReactElement, data: DashboardData) {
  const refresh = vi.fn(async () => undefined);
  return render(
    <DashboardProvider value={{ data, connection: 'online', refresh }}>
      <ConfirmProvider>{ui}</ConfirmProvider>
    </DashboardProvider>,
  );
}

type Handler = (path: string, init?: RequestInit) => unknown;

/** Stub fetch with JSON bodies keyed by path; a handler returning undefined yields a 404, a Response is used as-is. */
export function stubFetch(handler: Handler) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === '/api/v1/session') return new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 });
    const body = handler(path, init);
    if (body instanceof Error) throw body;
    if (body instanceof Response) return body;
    if (body === undefined) return new Response(JSON.stringify({ ok: false, error: 'not found' }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
