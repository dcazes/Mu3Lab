export interface Health { ok: boolean; version: string; }

export interface Service {
  id: string; name: string; category: string; lifecycle: 'always_on' | 'shared' | 'optional'; https_port: number;
  auth: 'oidc' | 'proxy' | 'local' | 'excluded'; profiles: string[]; dependencies: string[];
  availability: 'available' | 'blocked'; blocked_reason: string; stage: 'foundation' | 'planned' | 'blocked';
  route: 'ready' | 'pending' | 'unavailable'; routable: boolean; mcp: { exposed: boolean; risk: string };
  state: 'healthy' | 'blocked' | 'not_installed' | 'stopped_or_unhealthy'; detail: string; url: string;
  route_ready: boolean; compose_present: boolean;
}
export interface ServicesResponse { ok: boolean; tailnet_dns_name: string; services: Service[]; }
export interface CatalogProfile { id: string; name: string; description: string; services: string[]; }
export interface CatalogService { summary: string; category?: string; stage_label?: string; resource_guidance?: string; integrations?: string[]; }
export interface CatalogResponse { ok: boolean; profiles: CatalogProfile[]; services: Record<string, CatalogService>; }
export interface Metric { total: number; used: number; percent: number; }
export interface BackupReadiness { state?: string; detail?: string; repository_present?: boolean; [key: string]: unknown; }
export interface SystemResponse { ok: boolean; cpu_percent: number; docker_ready: boolean; tailnet_dns_name: string; runtime_root: string; memory: Metric; disk: Metric; backup: BackupReadiness; }
export interface IntegrationsResponse { ok: boolean; policy: string; integrations: { source: string; destination: string; kind: string }[]; }
export interface IdentityResponse { ok: boolean; control_plane_auth: string; detail: string; writes_enabled: boolean; }
export interface Job { id: string; kind: string; service_id: string; action: string; state: string; actor: string; created_at: string; updated_at: string; detail: string; }
export interface JobsResponse { ok: boolean; available: boolean; jobs: Job[]; }
export interface AuditEvent { id: number; job_id: string | null; actor: string; event: string; created_at: string; detail: string; }
export interface AuditResponse { ok: boolean; available: boolean; events: AuditEvent[]; }

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
