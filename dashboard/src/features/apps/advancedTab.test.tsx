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

function setup(update = release()) {
  const posts: unknown[] = [];
  stubFetch((path, init) => {
    if (path.endsWith('/updates')) return update;
    if (path.endsWith('/backups')) return backups;
    if (path.endsWith('/actions') && init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)));
      return { ok: true, job: { id: 'job', state: 'queued' } };
    }
    return undefined;
  });
  renderWithDashboard(<AdvancedTab service={mealie} />, dashboardData([mealie]));
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
});
