import type { DatesSetArg, EventInput, EventSourceFuncArg } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';
import interactionPlugin from '@fullcalendar/interaction';
import listPlugin from '@fullcalendar/list';
import FullCalendar from '@fullcalendar/react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, type CalendarEvents } from '../../../api';
import { useApi } from '../../../lib/useApi';
import { errorText } from '../../../lib/format';
import { Link } from '../../../lib/router';
import { FitList, useBox } from './size';
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
          By size: a wide, short widget opens on the week, a square or tall one on the month. A small widget shows your
          next events instead.
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

// FullCalendar draws both views: a month grid, and the week as a list of each day's events.
const FC_VIEW = { month: 'dayGridMonth', week: 'listWeek' } as const;
const clock = (date: Date) => date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/** Fetches the shown range from the same API as the Calendar page; FullCalendar asks again on each move. */
async function loadEvents(info: EventSourceFuncArg): Promise<EventInput[]> {
  const params = new URLSearchParams({ start: info.startStr, end: info.endStr, limit: '100' });
  const body = await api<CalendarEvents>(`/api/v1/calendar/events?${params}`);
  if (body.ok === false) throw new Error(body.error || 'The calendar could not be loaded.');
  return body.events.map((event) => ({
    id: event.id,
    title: event.title || 'Untitled',
    start: event.start,
    end: event.end,
    allDay: event.all_day,
  }));
}

/** The time, once a minute: the agenda drops what has ended and moves on to tomorrow at midnight. */
function useMinute() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

/** "3:00 PM" today, "Thu 27" after; "All day" for all-day events today. */
function when(event: CalendarEvents['events'][number], start: Date, first: Date) {
  const sameDay = dayKey(start) === dayKey(first);
  if (sameDay) return event.all_day ? 'All day' : clock(start);
  return start.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
}

/**
 * A small calendar: today's date, then the next events as rows, as many as fit, then "+N more".
 * Used when the widget is too narrow or short for a month (or a week list) to read well.
 */
function Agenda({ short, side }: { short: boolean; side: boolean }) {
  const now = useMinute();
  const first = new Date(now);
  first.setHours(0, 0, 0, 0);
  const until = new Date(first);
  until.setDate(until.getDate() + 30);
  const params = new URLSearchParams({ start: first.toISOString(), end: until.toISOString(), limit: '50' });
  const { data, error } = useApi<CalendarEvents>(`/api/v1/calendar/events?${params}`, { interval: 5 * 60_000 });
  const events = (data?.events ?? [])
    .map((event) => ({ event, start: new Date(event.start), end: new Date(event.end || event.start) }))
    // Still on: an all-day event lasts the day, a timed one until it ends.
    .filter(({ event, end }) => (event.all_day ? end.getTime() > first.getTime() : end.getTime() > now))
    .sort((a, b) => a.start.getTime() - b.start.getTime());
  const heading = new Date(now);
  return (
    <div className={`cal-agenda${short ? ' is-short' : ''}${side ? ' is-side' : ''}`}>
      <p className="cal-agenda-date">
        <strong>{heading.getDate()}</strong>
        <span>
          {heading.toLocaleDateString(undefined, { weekday: short && !side ? 'short' : 'long' })}
          {!short && <small>{heading.toLocaleDateString(undefined, { month: 'long' })}</small>}
        </span>
      </p>
      {error || data?.ok === false ? (
        <p className="widget-error">{error || data?.error || 'The calendar could not be loaded.'}</p>
      ) : !data ? (
        <p className="muted">Loading…</p>
      ) : events.length ? (
        <FitList
          className="cal-agenda-list"
          label="Coming up"
          more={(hidden) => <Link to="/calendar">+{hidden} more</Link>}
        >
          {events.map(({ event, start }) => (
            <li key={`${event.id}-${event.start}`} title={event.title || 'Untitled'}>
              <span className="cal-agenda-when">{when(event, start < first ? first : start, first)}</span>
              <span className="cal-agenda-title">{event.title || 'Untitled'}</span>
            </li>
          ))}
        </FitList>
      ) : (
        <p className="muted cal-agenda-empty">Nothing in the next 30 days.</p>
      )}
    </div>
  );
}

/**
 * The person's own calendar, the same one the Calendar page shows. With room, FullCalendar draws it
 * (as the page does): a month with as many events as each day has room for, or the week as a list;
 * picking a day, or a day's "+ more", opens its week. Without room for that, the next events.
 */
export function CalendarWidget({ widget }: WidgetProps) {
  const { width, height } = useBox();
  const measured = width > 0;
  if (measured && (width < 300 || height < 200))
    return <Agenda short={height < 120 || width < 220} side={width >= 440} />;
  return <FullCalendarWidget widget={widget} width={width} height={height} />;
}

function FullCalendarWidget({
  widget,
  width,
  height,
}: {
  widget: WidgetProps['widget'];
  width: number;
  height: number;
}) {
  const settings = widget.settings as CalendarWidgetSettings;
  const calendar = useRef<FullCalendar>(null);
  // By size: a wide, short widget opens on its week, a square or tall one on the month.
  const opensOn: View =
    settings.view === 'month' || settings.view === 'week' ? settings.view : width > height * 1.8 ? 'week' : 'month';
  // A month in a small widget leaves each day about one line: it marks its events with dots instead.
  const compact = width < 440 || height < 320;
  const narrow = width < 380;
  const [shown, setShown] = useState<{ view: View; start: Date; end: Date; title: string } | null>(null);
  const [problem, setProblem] = useState('');

  const show = (view: View, date?: Date) => calendar.current?.getApi().changeView(FC_VIEW[view], date);
  // Events come from FullCalendar's own fetching; this only checks again every five minutes.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') calendar.current?.getApi().refetchEvents();
    }, 5 * 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const view = shown?.view ?? opensOn;
  const now = new Date();
  const isToday = !shown || (now >= shown.start && now < shown.end);
  const short = (date: Date) => date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const title = !shown
    ? ''
    : view === 'month'
      ? narrow
        ? shown.start.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
        : shown.title
      : `${short(shown.start)} – ${short(new Date(shown.end.getTime() - 1))}`;

  return (
    <div className={`cal cal-${view}${compact ? ' cal-compact' : ''}`}>
      <div className="cal-bar">
        <span className="cal-title">{title}</span>
        <div className="cal-nav">
          <button type="button" aria-label={`Previous ${view}`} onClick={() => calendar.current?.getApi().prev()}>
            <ChevronLeft />
          </button>
          {!isToday && (
            <button type="button" className="cal-today" onClick={() => calendar.current?.getApi().today()}>
              Today
            </button>
          )}
          <button type="button" aria-label={`Next ${view}`} onClick={() => calendar.current?.getApi().next()}>
            <ChevronRight />
          </button>
        </div>
        <div className="segmented cal-views" role="radiogroup" aria-label="Calendar view">
          {(['month', 'week'] as const).map((value) => (
            <button key={value} type="button" role="radio" aria-checked={view === value} onClick={() => show(value)}>
              {narrow ? (value === 'month' ? 'M' : 'W') : value === 'month' ? 'Month' : 'Week'}
            </button>
          ))}
        </div>
      </div>
      {problem && (
        <p className="cal-problem">
          <span className="widget-error">{problem}</span>{' '}
          <Link to="/calendar" className="widget-link">
            Open calendar
          </Link>
        </p>
      )}
      <div className="cal-body">
        <FullCalendar
          ref={calendar}
          plugins={[dayGridPlugin, listPlugin, interactionPlugin]}
          initialView={FC_VIEW[opensOn]}
          headerToolbar={false}
          // Fills the widget; the month shrinks its rows to fit and the list scrolls.
          height="100%"
          expandRows
          firstDay={settings.week_start === 'monday' ? 1 : 0}
          fixedWeekCount={false}
          // As many events as each day has room for, then "+2"; a compact month shows only dots.
          dayMaxEvents={compact ? 0 : true}
          moreLinkContent={(info) =>
            compact ? (
              <span className="cal-dots" aria-label={`${info.num} ${info.num === 1 ? 'event' : 'events'}`}>
                {Array.from({ length: Math.min(info.num, 3) }, (_, index) => (
                  <i key={index} />
                ))}
              </span>
            ) : (
              // Always an element: FullCalendar keeps the old dots if this switches to plain text.
              <span>+{info.num}</span>
            )
          }
          moreLinkClick={FC_VIEW.week}
          dayHeaderFormat={{ weekday: 'narrow' }}
          eventTimeFormat={({ date }) => clock(new Date(date.year, date.month, date.day, date.hour, date.minute))}
          listDayFormat={{ weekday: 'short', day: 'numeric' }}
          listDaySideFormat={false}
          noEventsContent="Nothing on this week"
          events={loadEvents}
          eventSourceSuccess={(list) => {
            setProblem('');
            return list;
          }}
          eventSourceFailure={(error) => setProblem(errorText(error))}
          dateClick={(info) => {
            if (info.view.type === FC_VIEW.month) show('week', info.date);
          }}
          datesSet={(info: DatesSetArg) =>
            setShown({
              view: info.view.type === FC_VIEW.month ? 'month' : 'week',
              start: info.view.currentStart,
              end: info.view.currentEnd,
              title: info.view.title,
            })
          }
        />
      </div>
    </div>
  );
}
