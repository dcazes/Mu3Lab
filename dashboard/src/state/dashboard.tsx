import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { jobActive } from '../lib/jobs';
import { resolvePath, usePath } from '../lib/router';
import { isWorking } from '../lib/services';
import {
  api,
  ApiError,
  type AuditResponse,
  type BackupAttention,
  type CatalogResponse,
  type ChatStatus,
  type CoreSetupResponse,
  type IdentityResponse,
  type JobsResponse,
  onSignedOut,
  type ProvisioningResponse,
  type ServicesResponse,
  type SystemResponse,
  type SnapshotResponse,
} from '../api';

export interface DashboardData {
  services: ServicesResponse;
  catalog: CatalogResponse;
  system: SystemResponse;
  identity: IdentityResponse;
  jobs: JobsResponse;
  audit: AuditResponse;
  // Administrators only; empty while backups keep up with the schedule.
  backup_attention?: BackupAttention[];
  core: CoreSetupResponse;
  provisioning: ProvisioningResponse;
  chat: ChatStatus;
  /** The app that runs local models now (Ollama). */
  model_runner?: string;
  /** Home warnings this person dismissed, on every device; each hides until it changes. */
  dismissed?: string[];
}

export type Connection = 'connecting' | 'online' | 'offline' | 'signed_out' | 'server_error';

export interface DashboardValue {
  data: DashboardData;
  connection: Connection;
  errorStatus?: number;
  /** Data sources whose latest refresh failed, so what is shown may be older. */
  failedSources?: string[];
  refresh: () => Promise<void>;
}

export const emptyData: DashboardData = {
  services: { observed_at: '', ok: false, tailnet_dns_name: '', services: [] },
  catalog: { ok: false, profiles: [], services: {} },
  system: {
    ok: false,
    cpu_percent: 0,
    uptime_seconds: 0,
    docker_ready: false,
    tailnet_dns_name: '',
    runtime_root: '',
    memory: { total: 0, used: 0, percent: 0 },
    disk: { total: 0, used: 0, percent: 0 },
    backup: {},
  },
  identity: { ok: false, control_plane_auth: 'not_configured', detail: '', writes_enabled: false },
  jobs: { ok: false, available: false, jobs: [] },
  audit: { ok: false, available: false, events: [] },
  core: { ok: false, ready_to_run: false, services: [], missing_manifests: [], next_action: '' },
  provisioning: { ok: false, available: false, complete: false, phases: [] },
  chat: { ok: false, ready: false, url: '', authentication: '', mcp_enabled_count: 0, detail: '' },
};

// The page refreshes quickly only while something is in progress (an install, a backup, an app
// starting). A page showing app statuses refreshes every minute; any other page keeps a slow
// heartbeat that still shows a crash or a change made on another device.
// Opening a page, coming back to the tab, or any action refreshes at once.
const BUSY_POLL_MS = 3000;
const STATUS_POLL_MS = 60000;
const IDLE_POLL_MS = 300000;
// Opening a status page on a check over a minute old makes the worker check again; ask again
// shortly for its results, a few times, so a stopped worker never turns into fast polling.
const SETTLE_MS = 4000;
const SETTLE_AFTER_MS = 60000;
const SETTLE_TRIES = 3;
// A refresh that has not answered by then is abandoned, so the next one can run (review R22).
const POLL_TIMEOUT_MS = 20000;
// Once loaded, the page stays up (marked out of date) for this long through failed refreshes, retrying
// meanwhile, before the full-screen error: an app starting or stopping can briefly slow one refresh.
const RETRY_MS = 5000;
const ERROR_GRACE_MS = 30000;

/** Something is installing, starting, stopping or backing up: its progress shows as it happens. */
export function busy(data: DashboardData): boolean {
  return (
    data.jobs.jobs.some((job) => jobActive(job.state)) ||
    data.services.services.some((service) => isWorking(service) || service.backing_up) ||
    (data.provisioning.available && !data.provisioning.complete && !data.provisioning.blocked)
  );
}

/** Pages that show app statuses. The sidebar's summary, on every page, does not count. */
export function showsStatus(path: string): boolean {
  return (
    path === '/' ||
    path === '/apps' ||
    path.startsWith('/apps/') ||
    /^\/settings\/(ai|integrations|security|system|sign-in)$/.test(path)
  );
}

/** How long until the next refresh. ``settleTries``: quick re-asks already made for this check. */
export function nextRefresh(data: DashboardData, retrying: boolean, statusPage: boolean, settleTries = 0): number {
  if (retrying) return RETRY_MS;
  if (busy(data)) return BUSY_POLL_MS;
  if (!statusPage) return IDLE_POLL_MS;
  const observed = Date.parse(data.services.observed_at || '');
  const old = Number.isFinite(observed) && Date.now() - observed > SETTLE_AFTER_MS;
  return old && settleTries < SETTLE_TRIES ? SETTLE_MS : STATUS_POLL_MS;
}
const DashboardContext = createContext<DashboardValue | null>(null);

export function DashboardProvider({ value, children }: { value: DashboardValue; children: ReactNode }) {
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardValue {
  const value = useContext(DashboardContext);
  if (!value) throw new Error('useDashboard must be used inside DashboardProvider');
  return value;
}

/** Administrators run Mu3Lab; household members use it. */
export function useIsAdmin(): boolean {
  return Boolean(useDashboard().data.identity.is_admin);
}

const isSignedOut = (error: unknown) => error instanceof ApiError && error.kind === 'signed_out';
const failedConnection = (error: unknown): Connection =>
  isSignedOut(error)
    ? 'signed_out'
    : error instanceof ApiError && (error.kind === 'offline' || error.kind === 'timeout')
      ? 'offline'
      : 'server_error';

/** Polls the control plane and reports whether it is reachable and signed in. */
export function useDashboardLoader(): DashboardValue {
  const [data, setData] = useState<DashboardData>(emptyData);
  const [connection, setConnection] = useState<Connection>('connecting');
  const [errorStatus, setErrorStatus] = useState<number>();
  // Sources whose last refresh failed; their data on screen is from an earlier refresh.
  const [failedSources, setFailedSources] = useState<string[]>([]);
  const inFlight = useRef<Promise<void> | null>(null);
  const loaded = useRef(false);
  const failingSince = useRef(0);
  const [retrying, setRetrying] = useState(false);
  // Quick re-asks made for one check, so an old check is asked about only a few times.
  const [settle, setSettle] = useState({ observedAt: '', tries: 0 });
  const path = usePath();
  const statusPage = showsStatus(path);
  // Counts finished refreshes, so each one schedules the next even when nothing changed.
  const [refreshes, setRefreshes] = useState(0);

  /** Keep the last good page (marked out of date) through a brief failure; otherwise show the error screen. */
  const failed = useCallback((error: unknown, source: string) => {
    setErrorStatus(error instanceof ApiError ? error.status : undefined);
    setFailedSources([source]);
    failingSince.current ||= Date.now();
    setRetrying(true);
    if (!loaded.current || Date.now() - failingSince.current >= ERROR_GRACE_MS) setConnection(failedConnection(error));
  }, []);

  const load = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      try {
        await api('/api/health', undefined, POLL_TIMEOUT_MS);
      } catch (error) {
        if (isSignedOut(error)) {
          setData((previous) => ({ ...previous, identity: emptyData.identity }));
          setConnection('signed_out');
          return;
        }
        failed(error, 'health');
        return;
      }
      try {
        // A page showing app statuses asks the worker to check them if its last check is a minute old.
        const path = showsStatus(resolvePath(window.location.pathname))
          ? '/api/v1/snapshot?status=true'
          : '/api/v1/snapshot';
        const snapshot = await api<SnapshotResponse>(path, undefined, POLL_TIMEOUT_MS);
        setData(snapshot);
        setFailedSources([]);
      } catch (error) {
        if (isSignedOut(error)) {
          setErrorStatus(error instanceof ApiError ? error.status : undefined);
          setData((previous) => ({ ...previous, identity: emptyData.identity }));
          setConnection('signed_out');
          return;
        }
        failed(error, 'snapshot');
        return;
      }
      loaded.current = true;
      failingSince.current = 0;
      setRetrying(false);
      setConnection('online');
      setErrorStatus(undefined);
    })().finally(() => {
      inFlight.current = null;
      setRefreshes((count) => count + 1);
    });
    inFlight.current = run;
    return run;
  }, [failed]);

  useEffect(() => {
    const stop = onSignedOut(() => setConnection('signed_out'));
    void load();
    const resume = () => {
      if (document.visibilityState === 'visible') void load();
    };
    window.addEventListener('online', resume);
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => {
      stop();
      window.removeEventListener('online', resume);
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [load]);

  // Opening a page that shows app statuses refreshes at once (the first page already loads above).
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current && showsStatus(path)) void load();
    opened.current = true;
  }, [path, load]);

  const observedAt = data.services.observed_at || '';
  const tries = settle.observedAt === observedAt ? settle.tries : 0;
  const delay = nextRefresh(data, retrying, statusPage, tries);
  useEffect(() => {
    // A hidden tab asks nothing; showing it again refreshes at once (above).
    const timer = window.setTimeout(() => {
      if (delay === SETTLE_MS) setSettle({ observedAt, tries: tries + 1 });
      if (document.visibilityState === 'visible') void load();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [delay, observedAt, tries, load, refreshes]);

  return { data, connection, errorStatus, failedSources, refresh: load };
}
