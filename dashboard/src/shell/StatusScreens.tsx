import { CircleAlert, LogIn, RefreshCw, WifiOff } from 'lucide-react';
import { useEffect } from 'react';
import { Button } from '../components/Button';

const RELOADED_KEY = 'mu3lab.signInReloadedAt';
// One automatic retry per this long, so a sign-in that really ended never reloads in a loop.
const RELOAD_GAP_MS = 2 * 60 * 1000;

/**
 * The dashboard's gate token lasts a day, Authentik's sign-in a month. A plain reload renews the
 * first from the second without a password, so try that once before asking anyone to sign in.
 */
function useSilentSignIn() {
  useEffect(() => {
    let last: number;
    try {
      last = Number(window.sessionStorage.getItem(RELOADED_KEY) || 0);
    } catch {
      return; // Without storage we cannot tell a loop from a first try; the button is enough.
    }
    if (Date.now() - last < RELOAD_GAP_MS) return;
    const reload = () => {
      if (document.visibilityState !== 'visible') return;
      try {
        window.sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
      } catch {
        return;
      }
      window.location.reload();
    };
    reload();
    // A tab in the background waits until it is looked at again.
    document.addEventListener('visibilitychange', reload);
    return () => document.removeEventListener('visibilitychange', reload);
  }, []);
}

export function OfflineScreen({ retry }: { retry: () => void }) {
  return (
    <div className="status-screen">
      <div className="status-screen-card">
        <span className="status-screen-icon">
          <WifiOff />
        </span>
        <h1>Can’t reach Mu3Lab</h1>
        <p>
          Your dashboard is private to your tailnet. Make sure Tailscale is connected on this device, then try again.
        </p>
        <Button variant="primary" icon={RefreshCw} onClick={retry}>
          Try again
        </Button>
      </div>
    </div>
  );
}

export function SignedOutScreen() {
  useSilentSignIn();
  return (
    <div className="status-screen">
      <div className="status-screen-card">
        <span className="status-screen-icon">
          <LogIn />
        </span>
        <h1>Your session ended</h1>
        <p>Sign in again with Authentik to keep managing Mu3Lab.</p>
        <Button variant="primary" icon={LogIn} onClick={() => window.location.reload()}>
          Sign in again
        </Button>
      </div>
    </div>
  );
}

export function ServerErrorScreen({ retry, status }: { retry: () => void; status?: number }) {
  return (
    <div className="status-screen">
      <div className="status-screen-card">
        <span className="status-screen-icon">
          <CircleAlert />
        </span>
        <h1>Mu3Lab could not load your dashboard</h1>
        <p>
          The dashboard data request failed{status ? ` (HTTP ${status})` : ''}. Try again. If this continues, the
          administrator can check the Mu3Lab service logs.
        </p>
        <Button variant="primary" icon={RefreshCw} onClick={retry}>
          Try again
        </Button>
      </div>
    </div>
  );
}

export function LoadingScreen() {
  return (
    <div className="status-screen" aria-busy="true">
      <span className="brand-mark brand-mark-lg pulse">μ</span>
    </div>
  );
}
