import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../test/fixtures';
import { PowerOverlay, useTurningOff } from './PowerOverlay';
import { UserMenu } from './UserMenu';

function TurningOff() {
  return useTurningOff() ? <PowerOverlay /> : null;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('UserMenu', () => {
  it('opens settings, updates, releases and sign-out from the person’s name', async () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    stubFetch((path) => (path === '/api/v1/system/update' ? { ok: true, available: true, changes: [] } : undefined));
    const setTheme = vi.fn();
    renderWithDashboard(<UserMenu theme="system" setTheme={setTheme} />, dashboardData([]));
    fireEvent.click(screen.getByRole('button', { name: /Owner/ }));
    const menu = screen.getByRole('menu', { name: 'Owner menu' });
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Settings',
      'Get started',
      'Apps',
      'Update',
      'What’s new',
      'Restart Mu3Lab',
      'Turn off Mu3Lab',
      'Changelog',
      'GitHub',
      'Log out',
    ]);
    await waitFor(() => expect(within(menu).getByRole('menuitem', { name: 'Update available' })).toBeInTheDocument());
    fireEvent.click(within(menu).getByRole('radio', { name: 'Dark' }));
    expect(setTheme).toHaveBeenCalledWith('dark');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'What’s new' }));
    expect(window.location.pathname).toBe('/settings/whats-new');
  });

  it('says a restart rebuilds the page first when the dashboard’s code changed', async () => {
    const fetchMock = stubFetch((path) =>
      path === '/api/v1/system/update'
        ? { ok: true, available: false, changes: [], dashboard_outdated: true }
        : path === '/api/v1/system/restart'
          ? { ok: true, message: 'Rebuilding' }
          : undefined,
    );
    renderWithDashboard(<UserMenu theme="light" setTheme={vi.fn()} />, dashboardData([]));
    fireEvent.click(screen.getByRole('button', { name: /Owner/ }));
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Restart Mu3Lab' }));
    expect(await screen.findByText(/the page is rebuilt first/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Rebuild and restart' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path === '/api/v1/system/restart')).toBe(true));
  });

  it('turns Mu3Lab off after a warning, then follows it until the wake page answers', async () => {
    const fetchMock = stubFetch((path) =>
      path === '/api/v1/power/off'
        ? { ok: true, message: 'Mu3Lab is turning off.' }
        : path === '/api/v1/power'
          ? new Response(JSON.stringify({ state: 'turning_off', step: 'Stopping your apps', done: 4, total: 15 }), {
              headers: { 'Content-Type': 'application/json' },
            })
          : undefined,
    );
    renderWithDashboard(
      <>
        <UserMenu theme="light" setTheme={vi.fn()} />
        <TurningOff />
      </>,
      dashboardData([]),
    );
    fireEvent.click(screen.getByRole('button', { name: /Owner/ }));
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Turn off Mu3Lab' }));
    expect(await screen.findByText(/for everyone in the household/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path === '/api/v1/power/off')).toBe(true));
    expect(await screen.findByText('Stopping your apps… (4 of 15)')).toBeInTheDocument();
  });

  it('shows members no administration items', () => {
    stubFetch(() => undefined);
    const data = dashboardData([]);
    renderWithDashboard(<UserMenu theme="light" setTheme={vi.fn()} />, {
      ...data,
      identity: { ...data.identity, is_admin: false, role: 'member' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Owner/ }));
    const items = within(screen.getByRole('menu'))
      .getAllByRole('menuitem')
      .map((item) => item.textContent);
    expect(items).not.toContain('Update');
    expect(items).not.toContain('What’s new');
    expect(items).not.toContain('Restart Mu3Lab');
  });
});
