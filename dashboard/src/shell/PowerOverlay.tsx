import { Power } from 'lucide-react';
import { useEffect, useState, useSyncExternalStore } from 'react';
import type { PowerStatus } from '../api';

const POWER_PATH = '/api/v1/power';
const POLL_MS = 2000;
// Turning off normally takes a minute or two; past this, say how to get Mu3Lab back.
const SLOW_MS = 4 * 60 * 1000;

// Kept outside any component: as sign-in stops, the dashboard's own screens (signed out,
// offline) replace the page, and this view must stay on top of all of them.
let turningOff = false;
const listeners = new Set<() => void>();

/** Show the turning-off view from now until the page reloads onto the wake page. */
export function showTurningOff() {
  turningOff = true;
  listeners.forEach((listener) => listener());
}

export function useTurningOff(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => turningOff,
  );
}

/**
 * Covers the dashboard while Mu3Lab turns off, following its progress.
 *
 * Near the end the dashboard itself stops, and a moment later the wake page answers this same
 * address (its answer says `wake`). Then the page reloads, showing the wake page and its Turn on button.
 * If the wake page cannot start, Mu3Lab turns itself back on, and the dashboard answering `on`
 * again reloads the page too.
 */
export function PowerOverlay() {
  const [status, setStatus] = useState<PowerStatus>({ state: 'turning_off', step: 'Starting' });
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    let stopped = false;
    let begun = false;
    const poll = async () => {
      try {
        const response = await fetch(POWER_PATH, { cache: 'no-store', redirect: 'manual' });
        if (!response.headers.get('content-type')?.startsWith('application/json')) return;
        const body = (await response.json()) as PowerStatus & { wake?: boolean };
        if (stopped) return;
        if (body.wake || (begun && body.state === 'on')) {
          window.location.reload();
          return;
        }
        if (body.state !== 'on') {
          begun = true;
          setStatus(body);
        }
      } catch {
        // Sign-in and the web server are stopping; the wake page answers in a few seconds.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    const late = window.setTimeout(() => setSlow(true), SLOW_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.clearTimeout(late);
    };
  }, []);
  const share = status.total ? Math.max(4, Math.round((100 * (status.done ?? 0)) / status.total)) : 4;
  const backOn = status.state === 'turning_on';
  return (
    <div className="power-overlay" role="alertdialog" aria-modal="true" aria-labelledby="power-overlay-title">
      <div className="power-overlay-panel">
        <Power className="power-overlay-icon" aria-hidden="true" />
        <h2 id="power-overlay-title">{backOn ? 'Turning Mu3Lab back on' : 'Turning off Mu3Lab'}</h2>
        <p aria-live="polite">
          {status.step || 'Stopping'}…{status.total ? ` (${status.done ?? 0} of ${status.total})` : ''}
        </p>
        <div className="power-overlay-track" aria-hidden="true">
          <div className="power-overlay-bar" style={{ width: `${share}%` }} />
        </div>
        <p className="power-overlay-note">
          {slow
            ? 'This is taking longer than it should. If this page does not change, run ./start.sh in the Mu3Lab folder on the Mu3Lab computer.'
            : 'This takes about a minute. Then this address shows a page with a button to turn Mu3Lab back on.'}
        </p>
      </div>
    </div>
  );
}
