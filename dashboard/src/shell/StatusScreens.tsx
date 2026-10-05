import { CircleAlert, LogIn, RefreshCw, WifiOff } from 'lucide-react';
import { Button } from '../components/Button';

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
