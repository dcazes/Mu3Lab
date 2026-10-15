import { CalendarClock } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { type BackupSchedule, type BackupScheduleRequest, putJsonApi } from '../../api';
import { Button } from '../../components/Button';
import { Callout, Facts } from '../../components/Layout';
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

/**
 * How often Kopia's backup service backs up everything, in this server's local time.
 * A section of the Backups card; ``disabled`` while Kopia backups isn't installed.
 */
export function BackupScheduleSection({ disabled = false }: { disabled?: boolean }) {
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
  return (
    <section className="card-section" aria-labelledby="backup-schedule">
      <h3 id="backup-schedule">Schedule</h3>
      <p className="muted">
        Kopia backs up Mu3Lab and every installed app on its own, one at a time. Each app pauses for a moment while its
        data is copied, so choose a quiet time.
      </p>
      {!disabled && data.frequency === 'off' && (
        <Callout tone="warning" title="Scheduled backups are off">
          Apps are still backed up before every update, but nothing else protects new data.
        </Callout>
      )}
      {!disabled && !data.service_running && (
        <Callout tone="warning" title="The backup service is not running">
          No backup runs until Kopia backups is started again from Apps.
        </Callout>
      )}
      <form className="form schedule-form" onSubmit={(event) => void save(event)}>
        <fieldset disabled={disabled} className="plain-fieldset">
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
            <Button type="submit" icon={CalendarClock} loading={pending === 'schedule'} disabled={!changed}>
              Save schedule
            </Button>
          </div>
        </fieldset>
      </form>
      <Facts
        items={[
          { label: 'Schedule', value: describe(data) },
          {
            label: 'Next backup',
            value: !disabled && data.next_run_at ? new Date(data.next_run_at).toLocaleString() : '—',
          },
        ]}
      />
      {data.problems.length > 0 && (
        <>
          <p className="warning-text">These backups did not finish last time:</p>
          <ul className="muted">
            {data.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
