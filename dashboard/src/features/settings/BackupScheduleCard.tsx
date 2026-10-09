import { CalendarClock } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { type BackupSchedule, type BackupScheduleRequest, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { Callout, Card, Facts } from '../../components/Layout';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

const FREQUENCIES: { value: BackupScheduleRequest['frequency']; label: string }[] = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'daily', label: 'Daily' },
  { value: '12h', label: 'Every 12 hours' },
  { value: '6h', label: 'Every 6 hours' },
  { value: 'off', label: 'Off' },
];
// The server counts Monday as 0; the list starts on Sunday, as most calendars here do.
const DAYS = [6, 0, 1, 2, 3, 4, 5].map((value) => ({
  value,
  label: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][value],
}));

export function describe(schedule: Pick<BackupSchedule, 'frequency' | 'weekday' | 'time'>): string {
  const day = DAYS.find((item) => item.value === schedule.weekday)?.label ?? '';
  switch (schedule.frequency) {
    case 'weekly':
      return `Every ${day} at ${schedule.time}`;
    case 'daily':
      return `Every day at ${schedule.time}`;
    case '12h':
      return `Every 12 hours, from ${schedule.time}`;
    case '6h':
      return `Every 6 hours, from ${schedule.time}`;
    default:
      return 'Off';
  }
}

/** How often Mu3Lab backs up everything on its own, in this server's local time. */
export function BackupScheduleCard() {
  const { data, reload } = useApi<BackupSchedule>('/api/v1/system/backups/schedule');
  const { pending, run } = useAction();
  // Unsaved edits; until someone edits, the form shows the saved schedule.
  const [edits, setEdits] = useState<BackupScheduleRequest | null>(null);
  if (!data) return null;
  const draft = edits ?? { frequency: data.frequency, weekday: data.weekday, time: data.time };
  const changed = draft.frequency !== data.frequency || draft.weekday !== data.weekday || draft.time !== data.time;
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const saved = await run(
      'schedule',
      () => putJsonApi<BackupSchedule>('/api/v1/system/backups/schedule', draft),
      (result) => `Backups: ${describe(result).toLowerCase()}`,
    );
    if (saved) {
      setEdits(null);
      void reload();
    }
  };
  const last = data.last_run;
  return (
    <Card
      title="Backup schedule"
      description="Mu3Lab backs up itself and every installed app on its own. Sign-in, the password vault and chat pause for a few minutes while they are saved, so choose a quiet time."
    >
      {data.frequency === 'off' && (
        <Callout tone="warning" title="Scheduled backups are off">
          Apps are still backed up before every update and restore, but nothing else protects new data.
        </Callout>
      )}
      <form className="form schedule-form" onSubmit={(event) => void save(event)}>
        <label className="field">
          <span>How often</span>
          <select
            value={draft.frequency}
            onChange={(event) =>
              setEdits({ ...draft, frequency: event.target.value as BackupScheduleRequest['frequency'] })
            }
          >
            {FREQUENCIES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {draft.frequency === 'weekly' && (
          <label className="field">
            <span>Day</span>
            <select
              value={draft.weekday}
              onChange={(event) => setEdits({ ...draft, weekday: Number(event.target.value) })}
            >
              {DAYS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {draft.frequency !== 'off' && (
          <label className="field">
            <span>{draft.frequency === 'weekly' || draft.frequency === 'daily' ? 'Time' : 'Starting at'}</span>
            <input
              type="time"
              required
              value={draft.time}
              onChange={(event) => setEdits({ ...draft, time: event.target.value })}
            />
          </label>
        )}
        <div className="button-row">
          <Button type="submit" size="sm" icon={CalendarClock} loading={pending === 'schedule'} disabled={!changed}>
            Save schedule
          </Button>
        </div>
      </form>
      <Facts
        items={[
          { label: 'Schedule', value: describe(data) },
          {
            label: 'Next backup',
            value: data.next_run_at ? new Date(data.next_run_at).toLocaleString() : '—',
          },
          {
            label: 'Last scheduled backup',
            value: last
              ? last.finished < last.jobs
                ? `Running: ${last.finished} of ${last.jobs} done`
                : last.failed
                  ? `${relativeTime(last.scheduled_for)}: ${last.failed} of ${last.jobs} failed`
                  : `${relativeTime(last.scheduled_for)}: all ${last.jobs} saved`
              : 'None yet',
          },
        ]}
      />
      {last && (last.problems.length > 0 || last.skipped.length > 0) && (
        <ul className="muted">
          {last.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
          {last.skipped.map((item) => (
            <li key={item}>Skipped {item}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}
