import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
import { UpdateAll } from './UpdateAll';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const update = (installed: string, approved: string, files_only = false) => ({
  repository: 'example/app',
  installed_version: installed,
  approved_version: approved,
  update_available: true,
  supporting_only: false,
  files_only,
});

describe('Update all', () => {
  it('queues one backed-up update per app with an approved update, after a confirm', async () => {
    const posts: string[] = [];
    stubFetch((path, init) => {
      if (path.endsWith('/actions') && init?.method === 'POST') {
        posts.push(path);
        return { ok: true, job: { id: 'job', state: 'queued' } };
      }
      return {};
    });
    renderWithDashboard(
      <UpdateAll />,
      dashboardData([
        service('mealie', 'Mealie', 'optional', { update: update('v3.22.0', 'v3.23.0') }),
        service('grocy', 'Grocy', 'optional', { update: update('v4.7.1', 'v4.7.1', true) }),
        // Up to date, and foundation apps (updated by ./install.sh), are left out.
        service('wallos', 'Wallos', 'optional', { update: { ...update('v5', 'v5'), update_available: false } }),
        service('authentik', 'Authentik', 'foundation', { update: update('1', '2') }),
      ]),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Update all (2)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Update 2 apps?' });
    expect(dialog).toHaveTextContent('Mealie');
    expect(dialog).toHaveTextContent('v3.22.0 → v3.23.0');
    expect(dialog).toHaveTextContent('Grocy');
    expect(dialog).not.toHaveTextContent('Wallos');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update 2 apps' }));
    await waitFor(() => expect(posts).toEqual(['/api/v1/services/mealie/actions', '/api/v1/services/grocy/actions']));
  });

  it('is not offered when nothing has an update', () => {
    stubFetch(() => ({}));
    renderWithDashboard(<UpdateAll />, dashboardData([service('mealie', 'Mealie', 'optional')]));
    expect(screen.queryByRole('button', { name: /Update all/ })).not.toBeInTheDocument();
  });
});
