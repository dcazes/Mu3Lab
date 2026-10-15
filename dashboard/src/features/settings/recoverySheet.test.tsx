import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { groups, RecoverySheetSection } from './RecoverySheet';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PASSWORD = 'abcdefgh12345678ABCDEFGH-_-_-_-_ijklmnop87654321QRSTUVWXzyxwvuts';
const sheet = {
  ok: true,
  server: 'mu3lab.example.ts.net',
  generated_at: '2026-10-09T16:00:00+00:00',
  version: '0.1.0',
  password: PASSWORD,
  password_check: 'AB12-CD34',
  repository: '/srv/mu3lab/backups/kopia',
  kopia_image: 'docker.io/kopia/kopia:0.23.1@sha256:abc',
  copies: [],
};

function setup(status: { opened_at: string; opened_by: string } | null = null) {
  const posts: string[] = [];
  stubFetch((path, init) => {
    if (path === '/api/v1/system/backups/recovery-sheet' && init?.method === 'POST') {
      posts.push(path);
      return sheet;
    }
    return undefined;
  });
  const onOpened = vi.fn();
  renderWithDashboard(<RecoverySheetSection status={status} onOpened={onOpened} />, dashboardData([]));
  return { posts, onOpened };
}

describe('Recovery sheet', () => {
  it('asks to be printed until someone has opened it', () => {
    setup();
    expect(screen.getByText('Print your recovery sheet')).toBeTruthy();
    cleanup();
    setup({ opened_at: '2026-10-09T16:00:00+00:00', opened_by: 'dak' });
    expect(screen.queryByText('Print your recovery sheet')).toBeNull();
    expect(screen.getByText(/by dak/)).toBeTruthy();
  });

  it('shows the password only after a confirmation, grouped for reading back', async () => {
    const { posts, onOpened } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Open recovery sheet' }));
    expect(posts).toEqual([]);
    fireEvent.click(await screen.findByRole('button', { name: 'Show it' }));
    const dialog = await screen.findByRole('dialog', { name: 'Recovery sheet' });
    const password = within(dialog).getByRole('list', { name: /Backup password/ });
    expect(
      within(password)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(groups(PASSWORD));
    expect(within(dialog).getByText('AB12-CD34')).toBeTruthy();
    expect(within(dialog).getByText(/no copy on another computer yet/)).toBeTruthy();
    expect(posts).toEqual(['/api/v1/system/backups/recovery-sheet']);
    expect(onOpened).toHaveBeenCalled();
  });

  it('splits the password into groups of eight with nothing lost', () => {
    expect(groups(PASSWORD)).toHaveLength(8);
    expect(groups(PASSWORD).join('')).toBe(PASSWORD);
    expect(groups('short')).toEqual(['short']);
  });
});
