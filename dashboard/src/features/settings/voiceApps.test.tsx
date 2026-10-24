import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VoiceApps } from '../../api';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { VoiceAppsCard } from './VoiceAppsCard';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function apps(on: boolean): VoiceApps {
  return {
    ok: true,
    installed: true,
    apps: [
      { id: 'beaver-habits', title: 'Beaver Habits', description: 'Habits.', operators_only: false, on },
      { id: 'kopia-ui', title: 'Backups', description: 'Backups.', operators_only: true, on: true },
    ],
  };
}

describe('Apps the voice assistant can use', () => {
  it('switches one app off for voice', async () => {
    const puts: unknown[] = [];
    let current = apps(true);
    stubFetch((path, init) => {
      if (path === '/api/v1/ai/voice/apps/beaver-habits' && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        current = apps(false);
        return current;
      }
      if (path === '/api/v1/ai/voice/apps') return current;
      return undefined;
    });
    renderWithDashboard(<VoiceAppsCard />, dashboardData([]));
    const habits = await screen.findByRole('switch', { name: 'Voice can use Beaver Habits' });
    expect(habits).toBeChecked();
    expect(screen.getByText('Administrators')).toBeInTheDocument();
    fireEvent.click(habits);
    await vi.waitFor(() => expect(puts).toEqual([{ on: false }]));
    await vi.waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Voice can use Beaver Habits' })).not.toBeChecked(),
    );
  });

  it('points to LiveKit until it is installed', async () => {
    stubFetch((path) => (path === '/api/v1/ai/voice/apps' ? { ok: true, installed: false, apps: [] } : undefined));
    renderWithDashboard(<VoiceAppsCard />, dashboardData([]));
    expect(await screen.findByText('Install LiveKit from Apps to talk to the voice assistant.')).toBeInTheDocument();
  });
});
