import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Job } from '../../api';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { Mu3LabUpdates } from './SystemSettings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const status = (overrides = {}) => ({
  ok: true,
  version: '0.1.0',
  commit: 'a625b8b',
  date: '2026-10-01',
  available: true,
  behind: 2,
  changes: ['Approve Mealie v3.28.0', 'Fix a typo'],
  blocked_reason: '',
  checked_at: 1,
  ...overrides,
});

function setup(update = status(), jobs: Job[] = []) {
  const posts: string[] = [];
  stubFetch((path, init) => {
    if (path.startsWith('/api/v1/system/update') && init?.method === 'POST') {
      posts.push(path);
      return { ok: true, job: { id: 'job', state: 'queued' } };
    }
    if (path.startsWith('/api/v1/system/update')) return update;
    return undefined;
  });
  const data = dashboardData([]);
  data.jobs = { ...data.jobs, jobs };
  renderWithDashboard(<Mu3LabUpdates />, data);
  return posts;
}

const job = (overrides: Partial<Job>): Job => ({
  id: 'job',
  kind: 'update',
  service_id: 'mu3lab',
  action: 'self_update',
  state: 'succeeded',
  actor: 'owner',
  created_at: '',
  updated_at: '',
  detail: '',
  ...overrides,
});

describe('Mu3Lab updates', () => {
  it('lists what changed and updates after confirming', async () => {
    const posts = setup();
    expect(await screen.findByText('Approve Mealie v3.28.0')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Update Mu3Lab' }));
    const dialog = await screen.findByRole('dialog', { name: 'Update Mu3Lab?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }));
    await vi.waitFor(() => expect(posts).toEqual(['/api/v1/system/update']));
  });

  it('sends a copy with its own changes to the terminal', async () => {
    setup(status({ blocked_reason: 'This copy has edited files, so update it from a terminal.' }));
    expect(await screen.findByText('This copy has edited files, so update it from a terminal.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Update Mu3Lab' })).not.toBeInTheDocument();
  });

  it('offers a reload once the update is installed', async () => {
    setup(status({ available: false }), [job({ detail: 'Mu3Lab updated to b1 (2026-10-02).' })]);
    expect(await screen.findByText('Mu3Lab was updated')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('explains an update that needs administrator access', async () => {
    setup(status(), [job({ state: 'failed', detail: 'This update needs administrator access for: Docker.' })]);
    expect(await screen.findByText('This update needs administrator access for: Docker.')).toBeInTheDocument();
  });
});
