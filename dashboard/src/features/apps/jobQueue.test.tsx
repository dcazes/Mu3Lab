import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
import { JobQueueNotice } from './JobQueue';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const job = (id: string, action: string, state: string, queue_position: number | null = null) => ({
  id,
  kind: 'service',
  service_id: id,
  action,
  state,
  actor: 'admin',
  // Asked for in the same second: only the worker's own numbering tells their turns apart.
  created_at: '2026-11-26T10:00:00+00:00',
  updated_at: '2026-11-26T10:00:00+00:00',
  detail: '',
  queue_position,
});

const busy = (id: string, name: string, action: string, state: string, turn: number | null = null) =>
  service(id, name, 'optional', { display_state: 'working', last_job: job(id, action, state, turn) });

describe('Apps at work', () => {
  it('shows one row for many apps, opening to the order the worker takes them in', () => {
    stubFetch(() => ({}));
    const services = [
      busy('wallos', 'Wallos', 'stop', 'queued', 3),
      busy('immich', 'Immich', 'update', 'running'),
      busy('mealie', 'Mealie', 'update', 'queued', 1),
      busy('grocy', 'Grocy', 'start', 'queued', 2),
      service('jellyfin', 'Jellyfin', 'optional'),
    ];
    renderWithDashboard(<JobQueueNotice services={services} />, dashboardData(services));
    const notice = screen.getByRole('region', { name: 'Apps at work' });
    expect(notice).toHaveTextContent('Updating Immich');
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    // Only the arrow at the end opens the app at work; the rest of the bar opens the order.
    expect(within(notice).getByRole('link', { name: 'Open Immich' })).toHaveAttribute('href', '/apps/immich');
    fireEvent.click(within(notice).getByRole('button', { name: /Updating Immich.*3 waiting/ }));
    const rows = within(screen.getByRole('list')).getAllByRole('listitem');
    expect(rows.map((row) => row.textContent)).toEqual([
      'NowImmichUpdating…',
      '1MealieWaiting to update',
      '2GrocyWaiting to start',
      '3WallosWaiting to stop',
    ]);
  });

  it('is one plain row for a single app, and nothing when no app is at work', () => {
    stubFetch(() => ({}));
    const one = [busy('immich', 'Immich', 'restart', 'running')];
    renderWithDashboard(<JobQueueNotice services={one} />, dashboardData(one));
    expect(screen.getByRole('region', { name: 'Apps at work' })).toHaveTextContent('Restarting Immich');
    expect(screen.queryByRole('button', { name: /waiting/ })).not.toBeInTheDocument();
    cleanup();
    const idle = [service('immich', 'Immich', 'optional')];
    renderWithDashboard(<JobQueueNotice services={idle} />, dashboardData(idle));
    expect(screen.queryByRole('region', { name: 'Apps at work' })).not.toBeInTheDocument();
  });
});
