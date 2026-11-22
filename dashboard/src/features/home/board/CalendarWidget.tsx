import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { CalendarEvent, CalendarEvents } from '../../../api';
import { Link } from '../../../lib/router';
import { useApi } from '../../../lib/useApi';
import type { SettingsProps, WidgetProps } from './widgets';

type View = 'month' | 'week';
interface CalendarWidgetSettings {
  view?: 'auto' | View;
  week_start?: 'sunday' | 'monday';
}

const VIEWS = [
  { value: 'auto', label: 'By size' },
  { value: 'month', label: 'Month' },
  { value: 'week', label: 'Week' },
] as const;

// All-day events arrive as a bare date, which Date would read as midnight UTC (the day before, west of it).
const when = (value: string) => new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);

function startOfDay(date: Date) {
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);
  return day;
}

function addDays(date: Date, days: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

function weekOf(date: Date, firstDay: number) {
  const day = startOfDay(date);
  return addDays(day, -((day.getDay() - firstDay + 7) % 7));
}

/** The days an event covers, as day starts (an all-day event's end is the next day, exclusive). */
function coversDay(event: CalendarEvent, day: Date) {
  const start = when(event.start);
  const end = when(event.end);
  const next = addDays(day, 1);
  if (event.all_day) return start < next && end > day;
  return start < next && (end > day || start.getTime() === day.getTime());
}

const time = (value: string) => when(value).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

export function CalendarSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as CalendarWidgetSettings;
  return (
    <>
      <div className="widget-setting">
        <span className="widget-setting-label">Opens on</span>
        <div className="segmented" role="radiogroup" aria-label="Opens on">
          {VIEWS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={(settings.view || 'auto') === value}
              onClick={() => onChange({ ...settings, view: value })}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="muted widget-setting-hint">
          By size: a wide, short widget opens on the week, a square or tall one on the month.
        </p>
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Weeks start on</span>
        <div className="segmented" role="radiogroup" aria-label="Weeks start on">
          {(['sunday', 'monday'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={(settings.week_start || 'sunday') === value}
              onClick={() => onChange({ ...settings, week_start: value })}
            >
              {value === 'sunday' ? 'Sunday' : 'Monday'}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

/**
 * The person's own calendar, the same one the Calendar page shows: a month with a dot for each
 * event, or a week with each day's events stacked. Picking a day in the month opens its week.
 */
export function CalendarWidget({ widget }: WidgetProps) {
  const settings = widget.settings as CalendarWidgetSettings;
  const firstDay = settings.week_start === 'monday' ? 1 : 0;
  const opensOn: View =
    settings.view === 'month' || settings.view === 'week' ? settings.view : widget.w > widget.h ? 'week' : 'month';
  const [chosen, setView] = useState<View>(opensOn);
  // One row is too short for a month: a short widget always shows its week.
  const short = widget.h === 1;
  const view: View = short ? 'week' : chosen;
  // The day the view is anchored on: its month, or its week.
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const today = startOfDay(new Date());

  const range = useMemo(() => {
    if (view === 'week') {
      const start = weekOf(anchor, firstDay);
      return { start, days: Array.from({ length: 7 }, (_, index) => addDays(start, index)) };
    }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const start = weekOf(first, firstDay);
    const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const weeks = Math.ceil(((last.getTime() - start.getTime()) / 86_400_000 + 1) / 7);
    return { start, days: Array.from({ length: weeks * 7 }, (_, index) => addDays(start, index)) };
  }, [view, anchor, firstDay]);

  const path = useMemo(() => {
    const end = addDays(range.days[range.days.length - 1], 1);
    const params = new URLSearchParams({ start: range.start.toISOString(), end: end.toISOString(), limit: '100' });
    return `/api/v1/calendar/events?${params}`;
  }, [range]);
  const { data, error } = useApi<CalendarEvents>(path, { interval: 5 * 60_000 });
  const events = data?.ok === false ? [] : data?.events || [];
  const problem = error || (data?.ok === false ? data.error : '');

  const move = (step: number) =>
    setAnchor((current) =>
      view === 'week' ? addDays(current, step * 7) : new Date(current.getFullYear(), current.getMonth() + step, 1),
    );
  const title =
    view === 'month'
      ? anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
      : `${range.days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${range.days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
  const showsToday = range.days.some((day) => sameDay(day, today));
  const weekdays = range.days.slice(0, 7);

  return (
    <div className={`cal cal-${view} cal-h${widget.h}`}>
      <div className="cal-bar">
        <span className="cal-title">{title}</span>
        <div className="cal-nav">
          <button type="button" aria-label={`Previous ${view}`} onClick={() => move(-1)}>
            <ChevronLeft />
          </button>
          {!showsToday && (
            <button type="button" className="cal-today" onClick={() => setAnchor(today)}>
              Today
            </button>
          )}
          <button type="button" aria-label={`Next ${view}`} onClick={() => move(1)}>
            <ChevronRight />
          </button>
        </div>
        {!short && (
          <div className="segmented cal-views" role="radiogroup" aria-label="Calendar view">
            {(['month', 'week'] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={view === value}
                onClick={() => setView(value)}
              >
                {value === 'month' ? 'Month' : 'Week'}
              </button>
            ))}
          </div>
        )}
      </div>
      {problem ? (
        <div className="widget-empty">
          <p className="widget-error">{problem}</p>
          <Link to="/calendar" className="widget-link">
            Open calendar
          </Link>
        </div>
      ) : view === 'month' ? (
        <div className="cal-month" role="grid" aria-label={title}>
          {weekdays.map((day) => (
            <span key={`head-${day.getDay()}`} className="cal-weekday" role="columnheader">
              {day.toLocaleDateString(undefined, { weekday: 'narrow' })}
            </span>
          ))}
          {range.days.map((day) => {
            const count = events.filter((event) => coversDay(event, day)).length;
            const outside = day.getMonth() !== anchor.getMonth();
            return (
              <button
                key={day.toISOString()}
                type="button"
                role="gridcell"
                className={`cal-cell${outside ? ' is-outside' : ''}${sameDay(day, today) ? ' is-today' : ''}`}
                aria-label={`${day.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}: ${count ? `${count} ${count === 1 ? 'event' : 'events'}` : 'nothing on'}`}
                onClick={() => {
                  setAnchor(day);
                  setView('week');
                }}
              >
                <span className="cal-date">{day.getDate()}</span>
                <span className="cal-dots" aria-hidden>
                  {Array.from({ length: Math.min(count, 3) }, (_, index) => (
                    <i key={index} />
                  ))}
                  {count > 3 && <b>+</b>}
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <ol className="cal-week">
          {range.days.map((day) => {
            const list = events
              .filter((event) => coversDay(event, day))
              .sort(
                (a, b) => Number(b.all_day) - Number(a.all_day) || when(a.start).getTime() - when(b.start).getTime(),
              );
            return (
              <li
                key={day.toISOString()}
                className={`cal-day${sameDay(day, today) ? ' is-today' : ''}${day < today ? ' is-past' : ''}`}
              >
                <span className="cal-day-head">
                  <span>{day.toLocaleDateString(undefined, { weekday: 'short' })}</span>
                  <b>{day.getDate()}</b>
                </span>
                {list.length ? (
                  <ul>
                    {list.map((event) => (
                      <li key={event.id} title={event.title || 'Untitled'}>
                        <span className="cal-event-time">{event.all_day ? 'All day' : time(event.start)}</span>
                        <span className="cal-event-title">{event.title || 'Untitled'}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="cal-free" aria-label="Nothing on" />
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
