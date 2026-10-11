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
  service_running: true,
  problems: [] as string[],
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

  it('lists backups that did not finish and says when the backup service is down', async () => {
    setup({ ...weekly, service_running: false, problems: ['mealie: Another task is running on Mealie'] });
    expect(await screen.findByText('The backup service is not running')).toBeTruthy();
    expect(screen.getByText('mealie: Another task is running on Mealie')).toBeTruthy();
  });
});
