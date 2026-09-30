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
  postApi,
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

const SOURCES: { key: keyof DashboardData; path: string; operatorOnly?: boolean }[] = [
  { key: 'services', path: '/api/v1/services' },
  { key: 'catalog', path: '/api/v1/catalog' },
  { key: 'system', path: '/api/v1/system' },
  { key: 'identity', path: '/api/v1/identity' },
  { key: 'jobs', path: '/api/v1/jobs' },
  { key: 'audit', path: '/api/v1/audit' },
  { key: 'core', path: '/api/v1/setup/core' },
  { key: 'provisioning', path: '/api/v1/provisioning' },
  { key: 'chat', path: '/api/v1/chat/status', operatorOnly: true },
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

/** Polls the control plane and reports whether it is reachable and signed in. */
export function useDashboardLoader(): DashboardValue {
  const [data, setData] = useState<DashboardData>(emptyData);
  const [connection, setConnection] = useState<Connection>('connecting');
  const inFlight = useRef<Promise<void> | null>(null);
  const resumedOnboarding = useRef(false);

  const load = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      try {
        await api('/api/health');
      } catch (error) {
        setConnection(error instanceof ApiError && error.kind === 'signed_out' ? 'signed_out' : 'offline');
        return;
      }
      const results = await Promise.allSettled(SOURCES.map((source) => api<unknown>(source.path)));
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

  useEffect(() => {
    if (connection !== 'online' || !data.identity.writes_enabled || resumedOnboarding.current) return;
    resumedOnboarding.current = true;
    // Existing installations also get automatic first-login verification.
    void postApi('/api/v1/services/onboarding/resume').catch(() => {
      resumedOnboarding.current = false;
    });
  }, [connection, data.identity.writes_enabled]);

  return { data, connection, refresh: load };
}
