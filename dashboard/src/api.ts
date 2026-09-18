export interface Health { ok: boolean; version: string; }

export type LifecycleState = 'planned' | 'installing' | 'installed' | 'needs_setup' | 'configured' | 'starting' | 'ready' | 'stopped' | 'updating' | 'needs_attention' | 'blocked';
export interface Service {
  id: string; name: string; category: string; lifecycle: 'always_on' | 'shared' | 'optional'; https_port: number; private_https_port?: number;
  maturity: 'supported' | 'experimental' | 'planned';
  auth: 'oidc' | 'proxy' | 'local' | 'excluded'; profiles: string[]; dependencies: string[];
  availability: 'available' | 'blocked'; blocked_reason: string; stage: 'foundation' | 'core' | 'optional' | 'blocked';
  route: 'ready' | 'pending' | 'unavailable'; routable: boolean; required: boolean; identity_note: string;
  resource_guidance: string; setup_action: string; mcp: { exposed: boolean; risk: string };
  state: LifecycleState; lifecycle_state: LifecycleState; health_state: string; setup_state: string; route_state: string;
  identity_mode: string; backup_state: string; last_job_id: string; last_error: string; user_action: string;
  detail: string; url: string; route_ready: boolean; compose_present: boolean;
}
export interface ServicesResponse { ok: boolean; tailnet_dns_name: string; services: Service[]; }
export interface CatalogProfile { id: string; name: string; description: string; services: string[]; }
export interface CatalogService { summary: string; category?: string; stage_label?: string; resource_guidance?: string; integrations?: string[]; }
export interface CatalogResponse { ok: boolean; profiles: CatalogProfile[]; services: Record<string, CatalogService>; }
export interface Metric { total: number; used: number; percent: number; }
export interface BackupReadiness { state?: string; detail?: string; repository_present?: boolean; integrity_verified?: boolean; snapshot_present?: boolean; off_device?: boolean; last_verified_at?: string; [key: string]: unknown; }
export interface SystemResponse { ok: boolean; cpu_percent: number; docker_ready: boolean; tailnet_dns_name: string; runtime_root: string; memory: Metric; disk: Metric; backup: BackupReadiness; }
export interface IntegrationsResponse { ok: boolean; policy: string; integrations: { source: string; destination: string; kind: string }[]; }
export interface IdentityResponse { ok: boolean; control_plane_auth: string; username?: string; groups?: string[]; detail: string; writes_enabled: boolean; }
export interface CoreSetupResponse { ok: boolean; ready_to_run: boolean; services: string[]; missing_manifests: string[]; current_job?: Job | null; next_action: string; capacity?: { ok: boolean; reasons?: string[]; disk_free?: number; memory_total?: number; docker_ready?: boolean }; provisioning?: ProvisioningResponse | null; }
export interface ProvisioningPhase { phase_id: string; label: string; actual_state: string; detail: string; error: string; updated_at: string; attempts: number; }
export interface ProvisioningResponse { ok: boolean; available: boolean; complete: boolean; phases: ProvisioningPhase[]; waiting?: ProvisioningPhase | null; blocked?: ProvisioningPhase | null; }
export interface ProviderMetadata { id: string; label: string; updated_at: string; }
export interface ProviderMetadataResponse { ok: boolean; providers: ProviderMetadata[]; }
export interface Job { id: string; kind: string; service_id: string; action: string; state: string; actor: string; created_at: string; updated_at: string; detail: string; }
export interface JobsResponse { ok: boolean; available: boolean; jobs: Job[]; }
export interface AuditEvent { id: number; job_id: string | null; actor: string; event: string; created_at: string; detail: string; }
export interface AuditResponse { ok: boolean; available: boolean; events: AuditEvent[]; }

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

let csrfToken: Promise<string> | null = null;
function csrf(): Promise<string> {
  if (!csrfToken) csrfToken = api<{ csrf_token: string }>('/api/v1/session').then(result => result.csrf_token);
  return csrfToken;
}

export async function postApi<T>(path: string): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-Mu3Lab-CSRF': token } });
  if (!res.ok) throw new Error(`POST ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export async function postJsonApi<T>(path: string, body: unknown): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-Mu3Lab-CSRF': token }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`POST ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
