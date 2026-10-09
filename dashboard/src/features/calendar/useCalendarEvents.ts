import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api, ApiError, type CalendarEvents, type CalendarSync, postApi, postJsonApi, type Service } from '../../api';

export interface CalendarRange {
  start: string;
  end: string;
}

let autoConnectAttempted = false;

/**
 * Loads events for a range. Without a Nextcloud connection the API serves
 * Mu3Lab's own calendar; once Nextcloud is ready it is connected automatically.
 */
export function useCalendarEvents(range: CalendarRange | null, nextcloud: Service | undefined, limit = 100) {
  const [result, setResult] = useState<CalendarEvents | null>(null);
  const [loading, setLoading] = useState(true);
  const nextcloudState = nextcloud?.display_state;
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
      let body = await fetchEvents();
      if (body.pending_upload) {
        // Reading never sends anything; this explicit request moves events kept on Mu3Lab into Nextcloud.
        try {
          const report = await postJsonApi<CalendarSync>('/api/v1/calendar/sync', {});
          for (const title of report.kept)
            toast.info(`“${title}” was changed in Nextcloud after you deleted it here, so it was kept.`);
          if (report.conflicts.length)
            toast.warning(
              `${report.conflicts.length === 1 ? `“${report.conflicts[0]}” was` : `${report.conflicts.length} events were`} also changed in Nextcloud. Open it to choose which version to keep.`,
            );
          if (report.moved || report.kept.length || report.conflicts.length) body = await fetchEvents();
        } catch {
          /* Events stay on Mu3Lab and are sent on the next load. */
        }
      }
      if (body.state === 'local' && body.nextcloud_ready && nextcloudState === 'running' && !autoConnectAttempted) {
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

  const state = result?.state || 'loading';
  return { result, state, loading, reload: load };
}
