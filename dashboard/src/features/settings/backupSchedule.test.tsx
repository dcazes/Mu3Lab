import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { BackupScheduleCard, describe as describeSchedule } from './BackupScheduleCard';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const weekly = {
  ok: true,
  frequency: 'weekly' as const,
  weekday: 6,
  time: '03:30',
  next_run_at: '2026-10-11T07:30:00+00:00',
};

function setup(current: Record<string, unknown> = weekly) {
  const puts: unknown[] = [];
  stubFetch((path, init) => {
    if (path !== '/api/v1/system/backups/schedule') return undefined;
    if (init?.method === 'PUT') {
      const body = JSON.parse(String(init.body));
      puts.push(body);
      return { ...weekly, ...body };
    }
    return current;
  });
  renderWithDashboard(<BackupScheduleCard />, dashboardData([]));
  return puts;
}

describe('Backup schedule', () => {
  it('describes each schedule in plain words', () => {
    expect(describeSchedule(weekly)).toBe('Every Sunday at 03:30');
    expect(describeSchedule({ ...weekly, frequency: 'daily' })).toBe('Every day at 03:30');
    expect(describeSchedule({ ...weekly, frequency: '6h' })).toBe('Every 6 hours, from 03:30');
    expect(describeSchedule({ ...weekly, frequency: 'off' })).toBe('Off');
  });

  it('saves a new day and time, and only once something changed', async () => {
    const puts = setup();
    const save = await screen.findByRole('button', { name: 'Save schedule' });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Day'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Time'), { target: { value: '02:00' } });
    fireEvent.click(save);
    await vi.waitFor(() => expect(puts).toEqual([{ frequency: 'weekly', weekday: 5, time: '02:00' }]));
  });

  it('warns when scheduled backups are off and hides the day and time', async () => {
    setup({ ...weekly, frequency: 'off', next_run_at: '' });
    expect(await screen.findByText('Scheduled backups are off')).toBeTruthy();
    expect(screen.queryByLabelText('Day')).toBeNull();
    expect(screen.queryByLabelText('Time')).toBeNull();
  });

  it('reports a failed app from the last run', async () => {
    setup({
      ...weekly,
      last_run: {
        scheduled_for: '2026-10-04T07:30:00+00:00',
        jobs: 3,
        finished: 3,
        failed: 1,
        problems: ['Mealie was not backed up: disk full'],
        skipped: ['Immich: it was being set up or changed'],
      },
    });
    expect(await screen.findByText(/1 of 3 failed/)).toBeTruthy();
    expect(screen.getByText('Mealie was not backed up: disk full')).toBeTruthy();
    expect(screen.getByText('Skipped Immich: it was being set up or changed')).toBeTruthy();
  });
});
