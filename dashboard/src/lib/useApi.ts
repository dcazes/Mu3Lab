import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { errorText } from './format';

export interface ApiState<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => Promise<T | undefined>;
}

interface Loaded<T> {
  path: string | null;
  data: T | null;
  error: string;
  loading: boolean;
}

/**
 * Fetch a JSON resource, and again whenever the tab comes back into view.
 *
 * ``interval`` re-polls while the tab is visible. Give a function to poll only
 * while something is in progress: it receives the latest data and returns the
 * delay, or 0 to stop until the data changes again.
 *
 * What it returns always belongs to the current path: switching from one app
 * to another never shows the first app's data as the second's, and a request
 * for a path no longer shown is aborted (review R22).
 */
export function useApi<T>(
  path: string | null,
  { interval = 0 }: { interval?: number | ((data: T | null) => number) } = {},
): ApiState<T> {
  const [loaded, setLoaded] = useState<Loaded<T>>({ path, data: null, error: '', loading: Boolean(path) });
  // Only the newest request may update state, so a slow old response never wins.
  const latest = useRef(0);
  const running = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    if (!path) return;
    const request = ++latest.current;
    running.current?.abort();
    const controller = new AbortController();
    running.current = controller;
    try {
      const result = await api<T>(path, controller.signal);
      if (request !== latest.current) return;
      setLoaded({ path, data: result, error: '', loading: false });
      return result;
    } catch (cause) {
      if (request === latest.current && !controller.signal.aborted)
        setLoaded((previous) => ({
          path,
          data: previous.path === path ? previous.data : null,
          error: errorText(cause),
          loading: false,
        }));
    }
  }, [path]);

  useEffect(() => {
    if (!path) return;
    // Loading a resource is synchronization with an external system.
    void reload();
    // Coming back to the tab shows what changed meanwhile, without polling while it was hidden.
    const shown = () => {
      if (document.visibilityState === 'visible') void reload();
    };
    document.addEventListener('visibilitychange', shown);
    return () => {
      document.removeEventListener('visibilitychange', shown);
      // Whatever is still on its way belongs to a path no longer shown.
      latest.current += 1;
      running.current?.abort();
    };
  }, [path, reload]);

  const current = loaded.path === path ? loaded.data : null;
  const delay = typeof interval === 'function' ? interval(current) : interval;
  useEffect(() => {
    if (!path || !delay) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload();
    }, delay);
    return () => window.clearInterval(timer);
  }, [path, delay, reload]);

  if (loaded.path !== path) return { data: null, error: '', loading: Boolean(path), reload };
  return { data: loaded.data, error: loaded.error, loading: loaded.loading, reload };
}
