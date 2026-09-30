import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';
import interactionPlugin, { type DateClickArg } from '@fullcalendar/interaction';
import FullCalendar from '@fullcalendar/react';
import { CalendarDays, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import { type CalendarEvent, deleteApi, postJsonApi, putJsonApi } from '../../api';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Callout, EmptyState, PageHeader } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { useDashboard } from '../../state/dashboard';
import { type CalendarRange, useCalendarEvents } from './useCalendarEvents';

interface Draft {
  id?: string;
  revision?: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
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

export function CalendarPage() {
  const { data } = useDashboard();
  const nextcloud = data.services.services.find((service) => service.id === 'nextcloud');
  const [range, setRange] = useState<CalendarRange>(monthRange);
  const { result, state, loading, reload } = useCalendarEvents(range, nextcloud);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const confirm = useConfirm();
  const stale = state === 'stale';
  const ready = Boolean(result?.ok) || stale;
  const openUrl = nextcloud?.ui?.state === 'ready' && nextcloud.ui.url ? `${nextcloud.ui.url}/apps/calendar/` : '';

  const draftAt = (date: Date) => {
    const start = new Date(date);
    if (start.getHours() === 0) start.setHours(9, 0, 0, 0);
    setDraft({
      title: '',
      start: localInput(start),
      end: localInput(new Date(start.getTime() + 3600000)),
      all_day: false,
    });
  };
  const openEvent = (event: CalendarEvent) => {
    if (!event.editable || stale) {
      toast.info('Recurring events and offline snapshots are edited in Nextcloud Calendar.');
      return;
    }
    setDraft({
      id: event.id,
      revision: event.revision,
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
  const events: EventInput[] = (result?.events || []).map((event) => ({
    id: event.id,
    title: event.title,
    start: event.start,
    end: event.end,
    allDay: event.all_day,
    editable: Boolean(event.editable) && !stale,
    extendedProps: { calendarEvent: event },
  }));

  let content;
  if (state === 'not_installed')
    content = (
      <EmptyState
        icon={CalendarDays}
        title="No calendar yet"
        action={<LinkButton to="/apps/nextcloud">View Nextcloud</LinkButton>}
      >
        Install Nextcloud to keep a private calendar that syncs to your phone.
      </EmptyState>
    );
  else if (['not_connected', 'authentication_expired', 'sso_not_ready', 'service_stopped'].includes(state))
    content = (
      <EmptyState
        icon={CalendarDays}
        title={state === 'service_stopped' ? 'Nextcloud is stopped' : 'Connect your calendar'}
        action={
          <LinkButton variant="primary" to={state === 'service_stopped' ? '/apps/nextcloud' : '/settings/calendar'}>
            {state === 'service_stopped' ? 'Start Nextcloud' : 'Connect calendar'}
          </LinkButton>
        }
      >
        {state === 'authentication_expired'
          ? 'Nextcloud rejected the saved authorization. Reconnect to keep syncing.'
          : state === 'sso_not_ready'
            ? 'Nextcloud sign-in needs repair before its calendar can be connected.'
            : 'Connect your Nextcloud calendar in Settings. Mu3Lab uses an encrypted app credential; you do not need to enter your Nextcloud password.'}
      </EmptyState>
    );
  else
    content = (
      <div className="card calendar-card">
        {stale && (
          <Callout tone="warning" title="Showing your last sync">
            Nextcloud is temporarily unavailable.
          </Callout>
        )}
        {!ready && !loading && <p className="muted">{result?.error || 'Calendar is temporarily unavailable.'}</p>}
        <FullCalendar
          plugins={[dayGridPlugin, interactionPlugin]}
          initialView="dayGridMonth"
          headerToolbar={{ left: 'title', center: '', right: 'today prev,next' }}
          height="auto"
          fixedWeekCount={false}
          dayMaxEvents={3}
          events={events}
          datesSet={(info: DatesSetArg) => setRange({ start: info.startStr, end: info.endStr })}
          dateClick={(info: DateClickArg) => ready && !stale && draftAt(info.date)}
          eventClick={(info: EventClickArg) => {
            const event = info.event.extendedProps.calendarEvent as CalendarEvent | undefined;
            if (event) openEvent(event);
          }}
        />
      </div>
    );

  return (
    <div className="page">
      <PageHeader
        title={result?.calendar?.name || 'Calendar'}
        description="Synced privately from Nextcloud."
        actions={
          <>
            <Button
              variant="ghost"
              icon={RefreshCw}
              loading={loading}
              onClick={() => void reload()}
              aria-label="Refresh"
            />
            {openUrl && <ExternalButton href={openUrl}>Open in Nextcloud</ExternalButton>}
            {ready && (
              <Button variant="primary" icon={Plus} disabled={stale} onClick={() => draftAt(new Date())}>
                New event
              </Button>
            )}
          </>
        }
      />
      {content}
      <Dialog open={Boolean(draft)} onClose={() => setDraft(null)} title={draft?.id ? 'Edit event' : 'New event'}>
        {draft && (
          <form className="form" onSubmit={save} id="event-form">
            <label className="field">
              <span>Title</span>
              <input
                autoFocus
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
    </div>
  );
}
