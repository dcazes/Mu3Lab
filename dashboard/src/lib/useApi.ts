import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { errorText } from './format';

export interface ApiState<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => Promise<T | undefined>;
}

/** Fetch a JSON resource, optionally re-polling while the tab is visible. */
export function useApi<T>(path: string | null, { interval = 0 }: { interval?: number } = {}): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(Boolean(path));
  // Only the newest request may update state, so a slow old response never wins.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const request = ++latest.current;
    try {
      const result = await api<T>(path);
      if (request !== latest.current) return;
      setData(result);
      setError('');
      return result;
    } catch (cause) {
      if (request === latest.current) setError(errorText(cause));
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    if (!path) return;
    // Loading a resource is synchronization with an external system.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
    if (!interval) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload();
    }, interval);
    return () => window.clearInterval(timer);
  }, [path, interval, reload]);

  return { data, error, loading, reload };
}
