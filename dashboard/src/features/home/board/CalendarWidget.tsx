import { useEffect, useMemo, useState } from 'react';
import type { CalendarEvents } from '../../../api';
import { Link } from '../../../lib/router';
import { useApi } from '../../../lib/useApi';
import type { SettingsProps, WidgetProps } from './widgets';

const DAY_CHOICES = [1, 3, 7, 14] as const;

function dayKey(date: Date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayLabel(date: Date, now: number) {
  const today = new Date(now);
  const tomorrow = new Date(now);
  tomorrow.setDate(today.getDate() + 1);
  if (dayKey(date) === dayKey(today)) return 'Today';
  if (dayKey(date) === dayKey(tomorrow)) return 'Tomorrow';
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

// All-day events arrive as a bare date, which Date would read as midnight UTC (the day before, west of it).
const when = (value: string) => new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);

export function CalendarSettings({ widget, onChange }: SettingsProps) {
  const days = (widget.settings as { days?: number }).days || 7;
  return (
    <div className="widget-setting">
      <span className="widget-setting-label">Show the next</span>
      <div className="segmented" role="radiogroup" aria-label="Days to show">
        {DAY_CHOICES.map((choice) => (
          <button
            key={choice}
            type="button"
            role="radio"
            aria-checked={days === choice}
            onClick={() => onChange({ ...widget.settings, days: choice })}
          >
            {choice === 1 ? 'Today' : `${choice} days`}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The person's own upcoming events, from the same calendar the Calendar page shows. */
export function CalendarWidget({ widget }: WidgetProps) {
  const days = (widget.settings as { days?: number }).days || 7;
  const path = useMemo(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(start.getDate() + days);
    const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), limit: '30' });
    return `/api/v1/calendar/events?${params}`;
  }, [days]);
  const { data, error } = useApi<CalendarEvents>(path, { interval: 5 * 60_000 });
  // Finished events drop off as the day goes on.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  if (!data) return error ? <CalendarProblem text={error} /> : <p className="muted">Loading…</p>;
  if (!data.ok && data.error) return <CalendarProblem text={data.error} />;
  const upcoming = data.events.filter((event) => when(event.end).getTime() > now || event.all_day);
  if (!upcoming.length)
    return (
      <div className="widget-empty">
        <p>Nothing {days === 1 ? 'today' : `in the next ${days} days`}.</p>
        <Link to="/calendar" className="widget-link">
          Open calendar
        </Link>
      </div>
    );
  const groups = new Map<string, { label: string; events: typeof upcoming }>();
  for (const event of upcoming) {
    const start = when(event.start);
    const shown = start.getTime() < now ? new Date(now) : start;
    const key = dayKey(shown);
    if (!groups.has(key)) groups.set(key, { label: dayLabel(shown, now), events: [] });
    groups.get(key)!.events.push(event);
  }
  return (
    <div className="agenda">
      {[...groups.entries()].map(([key, group]) => (
        <section key={key}>
          <h4>{group.label}</h4>
          <ul>
            {group.events.map((event) => (
              <li key={event.id}>
                <span className="agenda-time">
                  {event.all_day
                    ? 'All day'
                    : when(event.start).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
                </span>
                <span className="agenda-title">{event.title || 'Untitled'}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function CalendarProblem({ text }: { text: string }) {
  return (
    <div className="widget-empty">
      <p className="widget-error">{text}</p>
      <Link to="/calendar" className="widget-link">
        Open calendar
      </Link>
    </div>
  );
}
