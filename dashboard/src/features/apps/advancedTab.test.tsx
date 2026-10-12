import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
import { AdvancedTab } from './AdvancedTab';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const mealie = service('mealie', 'Mealie', 'optional', {
  update: {
    repository: 'mealie-recipes/mealie',
    installed_version: 'v3.22.0',
    approved_version: 'v3.23.0',
    update_available: true,
    supporting_only: false,
  },
});

const release = (overrides = {}) => ({
  ok: true,
  repository: 'mealie-recipes/mealie',
  installed_version: 'v3.22.0',
  approved_version: 'v3.23.0',
  supporting_only: false,
  release_url: 'https://github.com/mealie-recipes/mealie/releases/tag/v3.23.0',
  update_available: true,
  update_enabled: true,
  blocked_reason: '',
  ...overrides,
});

const backups = {
  ok: true,
  service_id: 'mealie',
  readiness: {},
  backups: [
    {
      id: 'a'.repeat(64),
      short_id: 'aaaaaaaa',
      time: '2026-10-01T12:00:00Z',
      reason: 'pre-update',
      version: 'v3.22.0',
      paths: ['/data/mealie'],
    },
  ],
};

// Kopia backups installed: scheduled and on-demand backups are on.
const kopia = service('kopia-ui', 'Kopia backups', 'optional', { group: 'infrastructure' });

function setup(update = release(), app = mealie, jobs: unknown[] = [], others = [kopia]) {
  const posts: unknown[] = [];
  stubFetch((path, init) => {
    if (path.endsWith('/updates')) return update;
    if (path.endsWith('/backups')) return backups;
    if ((path.endsWith('/actions') || path.endsWith('/retry')) && init?.method === 'POST') {
      posts.push(path.endsWith('/retry') ? { retry: path } : JSON.parse(String(init.body)));
      return { ok: true, job: { id: 'job', state: 'queued' } };
    }
    return undefined;
  });
  renderWithDashboard(
    <AdvancedTab service={app} />,
    dashboardData([app, ...others], { jobs: { ok: true, available: true, jobs } } as never),
  );
  return posts;
}

describe('AdvancedTab updates', () => {
  it('updates after confirming', async () => {
    const posts = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Update to v3.23.0' }));
    const dialog = await screen.findByRole('dialog', { name: 'Update Mealie to v3.23.0?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }));
    await vi.waitFor(() => expect(posts).toEqual([{ action: 'update' }]));
  });

  it('explains why an update cannot run instead of offering it', async () => {
    setup(release({ update_enabled: false, blocked_reason: 'Updates need Docker to save a backup first.' }));
    expect(await screen.findByText('Updates need Docker to save a backup first.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Update to/ })).not.toBeInTheDocument();
  });

  it('names an update to supporting services plainly', async () => {
    const posts = setup(release({ approved_version: 'v3.22.0', supporting_only: true }));
    expect(
      await screen.findByText('A tested update is ready: updated supporting services for v3.22.0.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Release notes' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));
    const dialog = await screen.findByRole('dialog', { name: 'Update Mealie’s supporting services?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }));
    await vi.waitFor(() => expect(posts).toEqual([{ action: 'update' }]));
  });

  it('says when the app is on the latest tested version', async () => {
    setup(release({ update_available: false, update_enabled: false, approved_version: 'v3.22.0' }));
    expect(await screen.findByText('You’re on the latest tested version (v3.22.0).')).toBeInTheDocument();
  });
});

describe('AdvancedTab backups', () => {
  it('restores only after the app name is typed', async () => {
    const posts = setup();
    expect(await screen.findByText('Before an update (v3.22.0)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Restore…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Restore Mealie?' });
    const restore = within(dialog).getByRole('button', { name: 'Restore' });
    expect(restore).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Mealie' } });
    fireEvent.click(restore);
    await vi.waitFor(() =>
      expect(posts).toEqual([{ action: 'restore', snapshot_id: 'a'.repeat(64), confirm: 'Mealie' }]),
    );
  });

  it('after an interrupted restore offers only putting the original data back', async () => {
    const blocked = {
      ...mealie,
      recovery: {
        kind: 'restore' as const,
        detail: 'A restore of Mealie was interrupted before it finished.',
        since: '2026-10-08T10:00:00Z',
        can_keep: false,
      },
    };
    const posts = setup(release(), blocked);
    expect(await screen.findByText('Mealie needs attention.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back up now' })).toBeDisabled();
    expect(await screen.findByRole('button', { name: 'Restore…' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Keep new version' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Put my original data back' }));
    await vi.waitFor(() => expect(posts).toEqual([{ action: 'recover' }]));
  });

  it('without Kopia backups says backups are off and offers no Back up now', async () => {
    setup(release(), mealie, [], []);
    expect(await screen.findByText(/Backups are off, so Mealie is backed up only before updates/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Install Kopia backups' })).toHaveAttribute('href', '/apps/kopia-ui');
    expect(screen.queryByRole('button', { name: 'Back up now' })).not.toBeInTheDocument();
    // Earlier backups can still be restored.
    expect(await screen.findByRole('button', { name: 'Restore…' })).toBeEnabled();
  });

  it('after an interrupted update offers undoing it or keeping the new version', async () => {
    const blocked = {
      ...mealie,
      recovery: { kind: 'update' as const, detail: 'An update was interrupted.', since: '', can_keep: true },
    };
    const posts = setup(release(), blocked);
    fireEvent.click(await screen.findByRole('button', { name: 'Keep new version' }));
    await vi.waitFor(() => expect(posts).toEqual([{ action: 'keep_update' }]));
    expect(screen.getByRole('button', { name: 'Undo update' })).toBeInTheDocument();
  });

  it('says a failed restore put the data back and offers to try again', async () => {
    const failed = {
      id: 'r1',
      kind: 'lifecycle',
      service_id: 'mealie',
      action: 'restore',
      state: 'failed',
      actor: 'owner',
      created_at: '2026-10-08T10:00:00Z',
      updated_at: '2026-10-08T10:05:00Z',
      detail: 'Mealie did not start on the restored data.',
      error_code: 'restore_rolled_back',
    };
    const posts = setup(release(), mealie, [failed]);
    expect(
      await screen.findByText("The restore did not work, so Mealie's data was put back as it was."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(posts).toEqual([{ retry: '/api/v1/jobs/r1/retry' }]));
  });
});
