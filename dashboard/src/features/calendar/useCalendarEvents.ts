import { useCallback, useEffect, useState } from 'react';
import { api, ApiError, type CalendarEvents, postApi, type Service } from '../../api';

export interface CalendarRange {
  start: string;
  end: string;
}

let autoConnectAttempted = false;

/** Loads events for a range, connecting the calendar automatically once when Nextcloud is ready. */
export function useCalendarEvents(range: CalendarRange | null, nextcloud: Service | undefined, limit = 100) {
  const [result, setResult] = useState<CalendarEvents | null>(null);
  const [loading, setLoading] = useState(true);
  const nextcloudState = nextcloud?.state;
  const identityState = nextcloud?.identity?.state;

  const start = range?.start;
  const end = range?.end;
  const load = useCallback(async () => {
    if (!start || !end) return;
    const fetchEvents = async () => {
      const params = new URLSearchParams({ start, end, limit: String(limit) });
      try {
        const body = await api<CalendarEvents>(`/api/v1/calendar/events?${params}`);
        setResult(body);
        return body;
      } catch (error) {
        // The API reports calendar states (not connected, expired) with non-2xx codes.
        const detail = error instanceof ApiError ? error.body : {};
        const body: CalendarEvents = {
          ok: false,
          state: typeof detail.state === 'string' ? detail.state : 'unavailable',
          events: Array.isArray(detail.events) ? (detail.events as CalendarEvents['events']) : [],
          error: error instanceof Error ? error.message : String(error),
        };
        setResult(body);
        return body;
      }
    };
    setLoading(true);
    try {
      const body = await fetchEvents();
      if (body.state === 'not_connected' && nextcloudState === 'ready' && !autoConnectAttempted) {
        autoConnectAttempted = true;
        try {
          await postApi('/api/v1/calendar/auto-connect');
          await fetchEvents();
        } catch {
          /* Calendar settings stay available for an explicit connection. */
        }
      }
    } finally {
      setLoading(false);
    }
  }, [start, end, limit, nextcloudState]);

  useEffect(() => {
    if (!start) return;
    // Loading events is synchronization with an external system.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 300000);
    return () => window.clearInterval(timer);
  }, [load, start, identityState]);

  const installed = nextcloud && !['planned', 'not_installed', 'blocked'].includes(nextcloud.state);
  const state = !installed ? 'not_installed' : result?.state || 'loading';
  return { result, state, loading, reload: load };
}
