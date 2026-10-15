import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../test/fixtures';
import { UserMenu } from './UserMenu';

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
    ).toEqual(['Settings', 'Get started', 'Apps', 'Update', 'What’s new', 'Changelog', 'GitHub', 'Log out']);
    await waitFor(() => expect(within(menu).getByRole('menuitem', { name: 'Update available' })).toBeInTheDocument());
    fireEvent.click(within(menu).getByRole('radio', { name: 'Dark' }));
    expect(setTheme).toHaveBeenCalledWith('dark');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'What’s new' }));
    expect(window.location.pathname).toBe('/settings/whats-new');
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
  });
});
