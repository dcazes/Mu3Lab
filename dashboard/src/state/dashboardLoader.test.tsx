import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, service } from '../test/fixtures';
import { type DashboardData, nextRefresh, useDashboardLoader } from './dashboard';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('dashboard refresh', () => {
  it('lets a sign-out during a refresh win over the healthy probe', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        if (path === '/api/health') return new Response('{}', { status: 200 });
        // Authentik redirects every other request to its login page.
        return new Response(null, { status: 302, headers: { Location: '/login' } });
      }),
    );
    const { result } = renderHook(() => useDashboardLoader());
    await waitFor(() => expect(result.current.connection).toBe('signed_out'));
    expect(result.current.data.identity.writes_enabled).toBeFalsy();
  });

  it('refreshes quickly only while something is in progress', () => {
    const now = new Date().toISOString();
    const idle = dashboardData([service('immich', 'Immich', 'optional')]);
    idle.services.observed_at = now;
    // Nothing happening: a five-minute heartbeat.
    expect(nextRefresh(idle, false, false)).toBe(300000);
    const installing = dashboardData([service('immich', 'Immich', 'optional', { display_state: 'working' })]);
    installing.services.observed_at = now;
    expect(nextRefresh(installing, false, false)).toBe(3000);
    const job = { id: 'j1', state: 'queued' } as DashboardData['jobs']['jobs'][number];
    expect(nextRefresh({ ...idle, jobs: { ok: true, available: true, jobs: [job] } }, false, false)).toBe(3000);
    // A failed refresh is retried soon, whatever else is going on.
    expect(nextRefresh(idle, true, false)).toBe(5000);
  });

  it('asks once more shortly after opening on an old observation', () => {
    const old = dashboardData([]);
    old.services.observed_at = new Date(Date.now() - 120000).toISOString();
    expect(nextRefresh(old, false, false)).toBe(4000);
    // Only once per observation, so a stuck worker never turns into fast polling.
    expect(nextRefresh(old, false, true)).toBe(300000);
  });
});
