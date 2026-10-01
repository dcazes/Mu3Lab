import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import {
  api,
  ApiError,
  type AuditResponse,
  type CatalogResponse,
  type ChatStatus,
  type CoreSetupResponse,
  type IdentityResponse,
  type JobsResponse,
  onSignedOut,
  type ProvisioningResponse,
  type ServicesResponse,
  type SystemResponse,
} from '../api';

export interface DashboardData {
  services: ServicesResponse;
  catalog: CatalogResponse;
  system: SystemResponse;
  identity: IdentityResponse;
  jobs: JobsResponse;
  audit: AuditResponse;
  core: CoreSetupResponse;
  provisioning: ProvisioningResponse;
  chat: ChatStatus;
}

export type Connection = 'connecting' | 'online' | 'offline' | 'signed_out';

export interface DashboardValue {
  data: DashboardData;
  connection: Connection;
  /** Data sources whose latest refresh failed, so what is shown may be older. */
  failedSources?: string[];
  refresh: () => Promise<void>;
}

export const emptyData: DashboardData = {
  services: { ok: false, tailnet_dns_name: '', services: [] },
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

const SOURCES: { key: keyof DashboardData; path: string; adminOnly?: boolean }[] = [
  { key: 'services', path: '/api/v1/services' },
  { key: 'catalog', path: '/api/v1/catalog' },
  { key: 'system', path: '/api/v1/system' },
  { key: 'identity', path: '/api/v1/identity' },
  { key: 'jobs', path: '/api/v1/jobs' },
  { key: 'audit', path: '/api/v1/audit', adminOnly: true },
  { key: 'core', path: '/api/v1/setup/core' },
  { key: 'provisioning', path: '/api/v1/provisioning' },
  { key: 'chat', path: '/api/v1/chat/status' },
];

const POLL_MS = 10000;
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

/** Polls the control plane and reports whether it is reachable and signed in. */
export function useDashboardLoader(): DashboardValue {
  const [data, setData] = useState<DashboardData>(emptyData);
  const [connection, setConnection] = useState<Connection>('connecting');
  // Sources whose last refresh failed; their data on screen is from an earlier refresh.
  const [failedSources, setFailedSources] = useState<string[]>([]);
  // Learned from each refresh's identity; decides whether admin-only sources load.
  const isAdminRef = useRef(false);
  const inFlight = useRef<Promise<void> | null>(null);

  const load = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      try {
        await api('/api/health');
      } catch (error) {
        if (isSignedOut(error)) setData((previous) => ({ ...previous, identity: emptyData.identity }));
        setConnection(isSignedOut(error) ? 'signed_out' : 'offline');
        return;
      }
      // Household members never load administrator-only sources.
      const isAdmin = isAdminRef.current;
      const results = await Promise.allSettled(
        SOURCES.map((source) =>
          source.adminOnly && !isAdmin ? Promise.resolve(undefined) : api<unknown>(source.path),
        ),
      );
      // Signing out wins over a refresh that had already started: drop the
      // identity and its privileges rather than show stale operator state.
      if (results.some((result) => result.status === 'rejected' && isSignedOut(result.reason))) {
        setData((previous) => ({ ...previous, identity: emptyData.identity }));
        setConnection('signed_out');
        return;
      }
      const identityIndex = SOURCES.findIndex((source) => source.key === 'identity');
      const identity = results[identityIndex];
      if (identity.status === 'fulfilled' && identity.value && typeof identity.value === 'object')
        isAdminRef.current = Boolean((identity.value as { is_admin?: boolean }).is_admin);
      setFailedSources(
        SOURCES.filter((_source, index) => results[index].status === 'rejected').map((source) => source.key),
      );
      setData((previous) => {
        const next = { ...previous };
        results.forEach((result, index) => {
          const key = SOURCES[index].key;
          // Merge over defaults so a partial response can never crash a page.
          if (result.status === 'fulfilled' && result.value && typeof result.value === 'object')
            (next as Record<string, unknown>)[key] = { ...emptyData[key], ...(result.value as object) };
        });
        return next;
      });
      setConnection('online');
    })().finally(() => {
      inFlight.current = null;
    });
    inFlight.current = run;
    return run;
  }, []);

  useEffect(() => {
    const stop = onSignedOut(() => setConnection('signed_out'));
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, POLL_MS);
    const resume = () => void load();
    window.addEventListener('online', resume);
    window.addEventListener('focus', resume);
    return () => {
      stop();
      window.clearInterval(timer);
      window.removeEventListener('online', resume);
      window.removeEventListener('focus', resume);
    };
  }, [load]);

  return { data, connection, failedSources, refresh: load };
}
