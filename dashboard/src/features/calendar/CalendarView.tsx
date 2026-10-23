import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';
import interactionPlugin, { type DateClickArg } from '@fullcalendar/interaction';
import FullCalendar from '@fullcalendar/react';
import { CalendarDays, ChevronLeft, ChevronRight, Trash2 } from 'lucide-react';
import { type FormEvent, type ReactNode, useRef, useState } from 'react';
import { toast } from 'sonner';
import { type CalendarEvent, type CalendarEvents, deleteApi, postJsonApi, putJsonApi, type Service } from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Callout, EmptyState } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { Link } from '../../lib/router';
import { type CalendarRange, useCalendarEvents } from './useCalendarEvents';

interface Draft {
  id?: string;
  revision?: string;
  conflict?: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
}

export interface CalendarControls {
  result: CalendarEvents | null;
  loading: boolean;
  /** Events can be added and edited (connected to Nextcloud, or the local calendar). */
  ready: boolean;
  reload: () => Promise<void>;
  /** Opens the editor for a new event, today at the next round hour. */
  newEvent: () => void;
  /** One line on where events are kept, for the page's description. */
  status: ReactNode;
}

const pad = (value: number) => String(value).padStart(2, '0');
const localInput = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
const inputTime = (value: string, allDay: boolean) => (allDay ? value.slice(0, 10) : localInput(new Date(value)));

function monthRange(): CalendarRange {
  const now = new Date();
  return {
    start: new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
    end: new Date(now.getFullYear(), now.getMonth() + 1, 1).toISOString(),
  };
}

function nextcloudInstalled(nextcloud?: Service) {
  return Boolean(nextcloud && nextcloud.installed);
}

const dayKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
/** An all-day event's date as a local day; a timed event's moment. */
const eventDate = (value: string, allDay: boolean) =>
  allDay ? new Date(`${value.slice(0, 10)}T00:00`) : new Date(value);
const clock = (date: Date) => date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

function dayLabel(date: Date) {
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  if (dayKey(date) === dayKey(today)) return 'Today';
  if (dayKey(date) === dayKey(tomorrow)) return 'Tomorrow';
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

const UPCOMING = 8;

/** The next few events from now, by day: what most people open a calendar to see. */
function ComingUp({
  events,
  ready,
  open,
}: {
  events: CalendarEvent[];
  ready: boolean;
  open: (event: CalendarEvent) => void;
}) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const next = events
    .filter((event) => {
      const end = eventDate(event.end, event.all_day);
      // An all-day event's end is the day after it; a timed one still counts until it ends.
      return event.all_day ? end > startOfToday : end >= now;
    })
    .sort((a, b) => eventDate(a.start, a.all_day).getTime() - eventDate(b.start, b.all_day).getTime())
    .slice(0, UPCOMING);
  const days: { key: string; label: string; events: CalendarEvent[] }[] = [];
  for (const event of next) {
    const start = eventDate(event.start, event.all_day);
    // Something that began earlier and is still on shows under today.
    const shown = start < startOfToday ? startOfToday : start;
    const key = dayKey(shown);
    const day = days.find((item) => item.key === key);
    if (day) day.events.push(event);
    else days.push({ key, label: dayLabel(shown), events: [event] });
  }
  return (
    <aside className="card coming-up" aria-labelledby="coming-up-heading">
      <h2 id="coming-up-heading">Coming up</h2>
      {days.length ? (
        days.map((day) => (
          <section key={day.key} className="coming-up-day">
            <h3>{day.label}</h3>
            <ul>
              {day.events.map((event) => (
                <li key={event.id}>
                  <button type="button" onClick={() => open(event)}>
                    <span className="coming-up-time">
                      {event.all_day ? 'All day' : clock(eventDate(event.start, false))}
                    </span>
                    <span className="coming-up-title">
                      {event.conflict ? '⚠ ' : ''}
                      {event.title}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))
      ) : (
        <p className="muted">Nothing else this month.{ready ? ' Click a day on the calendar to add something.' : ''}</p>
      )}
    </aside>
  );
}

/**
 * The household calendar, shown with FullCalendar's month view.
 *
 * Whenever Nextcloud is not connected, events live in Mu3Lab's own calendar;
 * they move into Nextcloud automatically once it is connected, and are copied
 * back if it is disconnected or uninstalled.
 */
export function CalendarView({
  nextcloud,
  header,
  openSettings,
}: {
  nextcloud?: Service;
  header?: (controls: CalendarControls) => ReactNode;
  /** Opens calendar settings (connecting Nextcloud). */
  openSettings: () => void;
}) {
  const [range, setRange] = useState<CalendarRange>(monthRange);
  const { result, state, loading, reload } = useCalendarEvents(range, nextcloud);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const calendar = useRef<FullCalendar>(null);
  // The month shown, from the calendar itself ("October 2026").
  const [title, setTitle] = useState('');
  const confirm = useConfirm();
  const local = state === 'local';
  const stale = state === 'stale';
  const ready = Boolean(result?.ok) && (state === 'connected' || local);

  const draftAt = (date: Date, roundUp = false) => {
    const start = new Date(date);
    if (roundUp) start.setHours(start.getHours() + 1, 0, 0, 0);
    else if (start.getHours() === 0) start.setHours(9, 0, 0, 0);
    setDraft({
      title: '',
      start: localInput(start),
      end: localInput(new Date(start.getTime() + 3600000)),
      all_day: false,
    });
  };
  const openEvent = (event: CalendarEvent) => {
    if (!event.editable || stale) {
      toast.info(
        event.local
          ? 'Repeating events can be changed once Nextcloud is connected again.'
          : 'Recurring events and offline snapshots are edited in Nextcloud Calendar.',
      );
      return;
    }
    setDraft({
      id: event.id,
      revision: event.revision || undefined,
      conflict: event.conflict || undefined,
      title: event.title,
      start: inputTime(event.start, event.all_day),
      end: inputTime(event.end, event.all_day),
      all_day: event.all_day,
    });
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    try {
      const payload = {
        title: draft.title,
        start: draft.all_day ? draft.start : new Date(draft.start).toISOString(),
        end: draft.all_day ? draft.end : new Date(draft.end).toISOString(),
        all_day: draft.all_day,
        revision: draft.revision,
      };
      if (draft.id) await putJsonApi(`/api/v1/calendar/events/${draft.id}`, payload);
      else await postJsonApi('/api/v1/calendar/events', payload);
      setDraft(null);
      toast.success(draft.id ? 'Event updated' : 'Event added');
      await reload();
    } catch (error) {
      toast.error(errorText(error));
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (!draft?.id) return;
    if (!(await confirm({ title: 'Delete this event?', confirmLabel: 'Delete', tone: 'danger' }))) return;
    setSaving(true);
    try {
      await deleteApi(`/api/v1/calendar/events/${draft.id}`, { revision: draft.revision });
      setDraft(null);
      toast.success('Event deleted');
      await reload();
    } catch (error) {
      toast.error(errorText(error));
    } finally {
      setSaving(false);
    }
  };
  const resolve = async (keep: 'mine' | 'server') => {
    if (!draft?.id) return;
    setSaving(true);
    try {
      await postJsonApi(`/api/v1/calendar/events/${draft.id}/resolve`, { keep });
      setDraft(null);
      toast.success(keep === 'mine' ? 'Your version is now in Nextcloud' : 'Kept the Nextcloud version');
      await reload();
    } catch (error) {
      toast.error(errorText(error));
    } finally {
      setSaving(false);
    }
  };
  const events: EventInput[] = (result?.events || []).map((event) => ({
    id: event.id,
    title: event.conflict ? `⚠ ${event.title}` : event.title,
    start: event.start,
    end: event.end,
    allDay: event.all_day,
    editable: Boolean(event.editable) && !stale,
    extendedProps: { calendarEvent: event },
  }));

  const syncHint = nextcloudInstalled(nextcloud) ? (
    <>
      <button type="button" className="link-button" onClick={openSettings}>
        Connect Nextcloud
      </button>{' '}
      to sync them to your phone.
    </>
  ) : (
    <>
      <Link to="/apps/nextcloud">Install Nextcloud</Link> to sync them to your phone.
    </>
  );
  // Where events live, said once, in the page's description.
  const status = local ? (
    <>Kept privately on Mu3Lab. {syncHint}</>
  ) : stale ? (
    'Showing your last sync from Nextcloud.'
  ) : (
    'Synced privately with Nextcloud.'
  );
  const api = () => calendar.current?.getApi();

  let content;
  if (['authentication_expired', 'sso_not_ready', 'service_stopped'].includes(state))
    content = (
      <EmptyState
        icon={CalendarDays}
        title={state === 'service_stopped' ? 'Nextcloud is stopped' : 'Reconnect your calendar'}
        action={
          state === 'service_stopped' ? (
            <LinkButton variant="primary" to="/apps/nextcloud">
              Start Nextcloud
            </LinkButton>
          ) : (
            <Button variant="primary" onClick={openSettings}>
              Connect calendar
            </Button>
          )
        }
      >
        {state === 'authentication_expired'
          ? 'Nextcloud rejected the saved authorization. Reconnect to keep syncing.'
          : state === 'sso_not_ready'
            ? 'Nextcloud sign-in needs repair before its calendar can be shown.'
            : 'Start Nextcloud to see and edit your calendar.'}
      </EmptyState>
    );
  else
    content = (
      <div className="calendar-frame">
        <div className="calendar-layout">
          <div className="card calendar-card">
            <div className="calendar-toolbar">
              <h2>{title}</h2>
              <div className="button-row">
                <Button size="sm" onClick={() => api()?.today()}>
                  Today
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={ChevronLeft}
                  aria-label="Previous month"
                  onClick={() => api()?.prev()}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  icon={ChevronRight}
                  aria-label="Next month"
                  onClick={() => api()?.next()}
                />
              </div>
            </div>
            {stale && (
              <Callout tone="warning" title="Showing your last sync">
                Nextcloud is temporarily unavailable.
              </Callout>
            )}
            {!ready && !stale && !loading && result && (
              <p className="muted">{result.error || 'Calendar is temporarily unavailable.'}</p>
            )}
            <FullCalendar
              ref={calendar}
              plugins={[dayGridPlugin, interactionPlugin]}
              initialView="dayGridMonth"
              headerToolbar={false}
              height="auto"
              fixedWeekCount={false}
              dayMaxEvents={3}
              // Times as this browser writes them ("15:00" or "3:00 PM"), the same as Coming up.
              eventTimeFormat={({ date }) => clock(new Date(date.year, date.month, date.day, date.hour, date.minute))}
              events={events}
              datesSet={(info: DatesSetArg) => {
                setRange({ start: info.startStr, end: info.endStr });
                setTitle(info.view.title);
              }}
              dateClick={(info: DateClickArg) => ready && draftAt(info.date)}
              eventClick={(info: EventClickArg) => {
                const event = info.event.extendedProps.calendarEvent as CalendarEvent | undefined;
                if (event) openEvent(event);
              }}
            />
          </div>
          <ComingUp events={result?.events || []} ready={ready} open={openEvent} />
        </div>
      </div>
    );

  return (
    <>
      {header?.({ result, loading, ready, reload, newEvent: () => draftAt(new Date(), true), status })}
      {content}
      <Dialog open={Boolean(draft)} onClose={() => setDraft(null)} title={draft?.id ? 'Edit event' : 'New event'}>
        {draft && (
          <form className="form" onSubmit={save}>
            {draft.conflict && (
              <Callout
                tone="warning"
                title="Two versions of this event"
                action={
                  <div className="button-row">
                    <Button size="sm" disabled={saving} onClick={() => void resolve('mine')}>
                      Keep mine
                    </Button>
                    <Button size="sm" disabled={saving} onClick={() => void resolve('server')}>
                      Use Nextcloud’s
                    </Button>
                  </div>
                }
              >
                {draft.conflict} This one is yours, from Mu3Lab.
              </Callout>
            )}
            <label className="field">
              <span>Title</span>
              <input
                data-autofocus
                required
                maxLength={256}
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.all_day}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    all_day: event.target.checked,
                    start: event.target.checked ? draft.start.slice(0, 10) : `${draft.start.slice(0, 10)}T09:00`,
                    end: event.target.checked ? draft.end.slice(0, 10) : `${draft.end.slice(0, 10)}T10:00`,
                  })
                }
              />
              All day
            </label>
            <div className="field-row">
              <label className="field">
                <span>Starts</span>
                <input
                  required
                  type={draft.all_day ? 'date' : 'datetime-local'}
                  value={draft.start}
                  onChange={(event) => setDraft({ ...draft, start: event.target.value })}
                />
              </label>
              <label className="field">
                <span>Ends</span>
                <input
                  required
                  type={draft.all_day ? 'date' : 'datetime-local'}
                  value={draft.end}
                  onChange={(event) => setDraft({ ...draft, end: event.target.value })}
                />
              </label>
            </div>
            <footer className="form-footer">
              {draft.id && (
                <Button
                  variant="ghost"
                  icon={Trash2}
                  className="danger-text"
                  disabled={saving}
                  onClick={() => void remove()}
                >
                  Delete
                </Button>
              )}
              <span className="spacer" />
              <Button onClick={() => setDraft(null)}>Cancel</Button>
              <Button variant="primary" type="submit" loading={saving}>
                {draft.id ? 'Save' : 'Add event'}
              </Button>
            </footer>
          </form>
        )}
      </Dialog>
    </>
  );
}
