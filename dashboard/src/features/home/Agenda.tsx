import { CalendarDays } from 'lucide-react';
import { useMemo } from 'react';
import type { CalendarEvent, Service } from '../../api';
import { LinkButton } from '../../components/Button';
import { Link } from '../../lib/router';
import { useCalendarEvents } from '../calendar/useCalendarEvents';

const DAYS_AHEAD = 14;

function dayLabel(date: Date) {
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const diff = Math.round(
    (new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() - start.getTime()) / 86400000,
  );
  if (diff <= 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function eventTime(event: CalendarEvent) {
  if (event.all_day) return 'All day';
  return new Date(event.start).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function Agenda({ nextcloud }: { nextcloud?: Service }) {
  const range = useMemo(() => {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return {
      start: start.toISOString(),
      end: new Date(start.getTime() + DAYS_AHEAD * 86400000).toISOString(),
    };
  }, []);
  const { result, state, loading } = useCalendarEvents(range, nextcloud, 8);
  const events = (result?.events || []).filter((event) => new Date(event.end) >= new Date()).slice(0, 6);
  const groups = events.reduce<Record<string, CalendarEvent[]>>((acc, event) => {
    const label = dayLabel(new Date(event.start));
    (acc[label] ||= []).push(event);
    return acc;
  }, {});

  let body;
  if (state === 'not_installed')
    body = (
      <p className="agenda-empty">
        Install Nextcloud to see your calendar here. <Link to="/apps/nextcloud">View Nextcloud</Link>
      </p>
    );
  else if (['not_connected', 'authentication_expired', 'sso_not_ready', 'service_stopped'].includes(state))
    body = (
      <div className="agenda-empty">
        <span>
          {state === 'service_stopped'
            ? 'Nextcloud is stopped.'
            : state === 'authentication_expired'
              ? 'Calendar access expired.'
              : 'Your calendar isn’t connected yet.'}
        </span>
        <LinkButton size="sm" to={state === 'service_stopped' ? '/apps/nextcloud' : '/settings/calendar'}>
          {state === 'service_stopped' ? 'Open Nextcloud settings' : 'Connect calendar'}
        </LinkButton>
      </div>
    );
  else if (loading && !result) body = <p className="agenda-empty">Loading events…</p>;
  else if (state !== 'connected' && state !== 'stale')
    body = <p className="agenda-empty">{result?.error || 'Calendar events are temporarily unavailable.'}</p>;
  else if (!events.length)
    body = (
      <p className="agenda-empty">
        {state === 'stale'
          ? 'No upcoming events in the last calendar sync.'
          : 'No upcoming events in the next two weeks.'}
      </p>
    );
  else
    body = (
      <div className="agenda-list">
        {Object.entries(groups).map(([label, items]) => (
          <div key={label} className="agenda-day">
            <h3>{label}</h3>
            {items.map((event) => (
              <div key={event.id} className="agenda-event">
                <time>{eventTime(event)}</time>
                <span>{event.title}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    );

  return (
    <section aria-labelledby="agenda-heading">
      <div className="section-title">
        <h2 id="agenda-heading">
          <CalendarDays /> Up next
        </h2>
        <Link to="/calendar">Open calendar</Link>
      </div>
      <div className="card agenda">{body}</div>
    </section>
  );
}
