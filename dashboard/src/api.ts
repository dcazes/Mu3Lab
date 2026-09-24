export interface Health { ok: boolean; version: string; }

export type LifecycleState = 'planned' | 'not_installed' | 'config_required' | 'queued' | 'installing' | 'installed' | 'needs_setup' | 'configured' | 'starting' | 'verifying' | 'running' | 'ready' | 'stopped' | 'updating' | 'degraded' | 'failed' | 'needs_attention' | 'blocked';
export interface Service {
  id: string; name: string; category: string; lifecycle: 'always_on' | 'shared' | 'optional'; https_port: number; private_https_port?: number;
  maturity: 'supported' | 'experimental' | 'planned';
  auth: 'oidc' | 'proxy' | 'trusted_header' | 'local' | 'excluded'; profiles: string[]; dependencies: string[];
  availability: 'available' | 'blocked'; blocked_reason: string; stage: 'foundation' | 'core' | 'optional' | 'blocked';
  route: 'ready' | 'pending' | 'unavailable'; routable: boolean; required: boolean; identity_note: string;
  resource_guidance: string; setup_action: string; mcp: { exposed: boolean; risk: string };
  state: LifecycleState; lifecycle_state: LifecycleState; health_state: string; setup_state: string; route_state: string;
  installation_state?: 'not_installed' | 'partial' | 'installed' | 'restore_available' | 'failed_setup'; operational_state?: string; recommended_action?: string;
  identity_mode: string; backup_state: string; last_job_id: string; last_error: string; user_action: string;
  detail: string; url: string; route_ready: boolean; compose_present: boolean;
  ui?: { state: 'ready' | 'route_pending' | 'unavailable'; url: string | null; label: string; authentication: string; reason: string | null };
  // These fields were added with the v1 operator surface. Keep them optional
  // while an already-running control plane is being upgraded: the static
  // dashboard can be refreshed before the Python process is restarted.
  allowed_actions?: Array<'install' | 'retry_setup' | 'start' | 'stop' | 'restart' | 'repair'>; last_job?: Job | null;
  update?: { repository: string; current_version: string };
  configuration?: ServiceConfigField[];
  account?: { mode: string; handoff: boolean; user_action: string };
  initialization?: { mode: string; state: string; job_id?: string; verified_at?: string; last_error?: Record<string, unknown> };
  identity?: ServiceIdentity;
  containers?: Array<{ service: string; name: string; state: string; status: string; health: string; image: string }>;
}
export interface ServiceIdentity { mode: 'native_oidc' | 'trusted_header' | 'proxy_gate' | 'local' | 'none'; state: 'unconfigured' | 'configuring' | 'migration_required' | 'ready' | 'degraded' | 'unsupported'; launch_url: string; detail: string; last_verified_at: string; recovery_available: boolean; job_id: string; error?: Record<string, unknown>; }
export interface ServiceConfigField { key: string; type: 'string' | 'boolean' | 'integer' | 'enum' | 'secret'; label?: string; required?: boolean; default?: string | boolean | number; options?: string[]; value?: string | boolean | number | null; secret_present?: boolean; }
export interface ServiceConfigResponse { ok: boolean; service_id: string; fields: ServiceConfigField[]; restart_required?: boolean; }
export interface ServicesResponse { ok: boolean; tailnet_dns_name: string; services: Service[]; }
export interface CatalogProfile { id: string; name: string; description: string; services: string[]; }
export interface CatalogService { summary: string; category?: string; stage_label?: string; resource_guidance?: string; integrations?: string[]; }
export interface CatalogResponse { ok: boolean; profiles: CatalogProfile[]; services: Record<string, CatalogService>; }
export interface Metric { total: number; used: number; percent: number; }
export interface BackupReadiness { state?: string; detail?: string; repository_present?: boolean; integrity_verified?: boolean; snapshot_present?: boolean; off_device?: boolean; last_verified_at?: string; [key: string]: unknown; }
export interface SystemResponse { ok: boolean; cpu_percent: number; uptime_seconds?: number; docker_ready: boolean; tailnet_dns_name: string; runtime_root: string; memory: Metric; disk: Metric; backup: BackupReadiness; }
export interface IntegrationsResponse { ok: boolean; policy: string; integrations: { source: string; destination: string; kind: string }[]; }
export interface IdentityResponse { ok: boolean; control_plane_auth: string; username?: string; subject_id?: string; email?: string; display_name?: string; groups?: string[]; detail: string; writes_enabled: boolean; }
export interface CoreSetupResponse { ok: boolean; ready_to_run: boolean; services: string[]; missing_manifests: string[]; current_job?: Job | null; next_action: string; capacity?: { ok: boolean; reasons?: string[]; disk_free?: number; memory_total?: number; docker_ready?: boolean }; provisioning?: ProvisioningResponse | null; }
export interface ProvisioningPhase { phase_id: string; label: string; actual_state: string; detail: string; error: string; updated_at: string; attempts: number; }
export interface ProvisioningAction { kind: 'none' | 'link' | 'job' | 'bootstrap'; label: string; href?: string; endpoint?: string; }
export interface ProvisioningResponse { ok: boolean; available: boolean; complete: boolean; phases: ProvisioningPhase[]; waiting?: ProvisioningPhase | null; blocked?: ProvisioningPhase | null; progress?: { completed: number; total: number }; next_action?: ProvisioningAction; }
export interface ProviderCatalogItem { id: string; name: string; key_hint: string; prefix: string; instructions: string; probe_models?: string[]; example_models: string[]; }
export interface ProviderMetadata { id: string; name: string; label: string; enabled: boolean; state: 'saved' | 'verifying' | 'verified' | 'degraded' | 'disabled' | 'unsupported_legacy'; key_hint: string; credential_indicator: string; model_samples: string[]; models_are_examples: boolean; last_attempt_at: string; last_verified_at: string; updated_at: string; active_job_id: string; error: string; error_code?: string; recommended_action?: string; routed_via?: string; supported: boolean; }
export interface CalendarConnection { ok: boolean; state: 'not_installed' | 'service_stopped' | 'sso_not_ready' | 'not_connected' | 'awaiting_approval' | 'connected' | 'authentication_expired' | 'unavailable'; username_hint: string; selected_calendar_id: string; calendars: Array<{ id: string; name: string }>; last_success_at: string; error: string; }
export interface CalendarAuthorization { ok: boolean; state: 'awaiting_user' | 'pending' | 'connected' | 'expired' | 'failed'; authorization_id?: string; login_url?: string; expires_at?: string; poll_after_ms?: number; connection?: CalendarConnection; error?: string; }
export interface CalendarEvent { id: string; title: string; start: string; end: string; all_day: boolean; editable?: boolean; revision?: string; }
export interface CalendarEvents { ok: boolean; state: string; calendar?: { id: string; name: string }; fetched_at?: string; events: CalendarEvent[]; error?: string; }
export interface ProviderMetadataResponse { ok: boolean; providers: ProviderMetadata[]; }
export interface Job { id: string; kind: string; service_id: string; action: string; state: string; actor: string; created_at: string; updated_at: string; detail: string; step_id?: string; error_code?: string; }
export interface JobsResponse { ok: boolean; available: boolean; jobs: Job[]; }
export interface AuditEvent { id: number; job_id: string | null; actor: string; event: string; created_at: string; detail: string; }
export interface AuditResponse { ok: boolean; available: boolean; events: AuditEvent[]; }

export async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

async function errorMessage(res: Response, method: string, path: string): Promise<string> {
  try { const body = await res.json() as { error?: string | { message?: string; recommended_action?: string } }; if (typeof body.error === 'string') return body.error; if (body.error?.message) return `${body.error.message}${body.error.recommended_action ? ` ${body.error.recommended_action}` : ''}`; } catch { /* use HTTP fallback */ }
  return `${method} ${path}: HTTP ${res.status}`;
}

let csrfToken: Promise<string> | null = null;
function csrf(): Promise<string> {
  if (!csrfToken) csrfToken = api<{ csrf_token: string }>('/api/v1/session').then(result => result.csrf_token);
  return csrfToken;
}

export async function postApi<T>(path: string): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-Mu3Lab-CSRF': token } });
  if (!res.ok) throw new Error(await errorMessage(res, 'POST', path));
  return res.json() as Promise<T>;
}

export async function postJsonApi<T>(path: string, body: unknown): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-Mu3Lab-CSRF': token }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(await errorMessage(res, 'POST', path));
  return res.json() as Promise<T>;
}

export async function putJsonApi<T>(path: string, body: unknown): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'PUT', headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Mu3Lab-CSRF': token }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(await errorMessage(res, 'PUT', path));
  return res.json() as Promise<T>;
}

export async function deleteApi<T>(path: string, body?: unknown): Promise<T> {
  const token = await csrf();
  const res = await fetch(path, { method: 'DELETE', headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-Mu3Lab-CSRF': token }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) throw new Error(await errorMessage(res, 'DELETE', path));
  return res.json() as Promise<T>;
}

export interface ServiceLogsResponse { ok: boolean; service_id: string; container: string; lines: string[]; }
export interface JobDetailResponse { ok: boolean; job: Job; events: AuditEvent[]; }
export interface UpdateResponse { ok: boolean; repository: string; current_version: string; latest_version: string; release_url: string; published_at: string; release_name: string; notes: string; update_available: boolean; update_enabled: boolean; blocked_reason: string; checked_at: number; }
export interface McpServer { id: string; name: string; service_id: string; kind: string; transport: string; app_state: string; enabled: boolean; prepared?: boolean; setup_mode?: 'automatic' | 'manual'; setup_detail?: string; state: 'live' | 'degraded' | 'authentication_required' | 'disabled' | 'prepared' | 'unavailable' | 'starting' | 'incompatible' | 'failed' | 'stopped'; error?: string | null; last_verified_at?: string; auth: { type: string; scopes: string[]; configured: boolean }; review?: { status: string; repository: string; revision: string; preferred: boolean; note?: string }; configuration?: ServiceConfigField[]; tools: Array<{ id: string; title: string; risk: string; enabled: boolean; permission?: string; parameters?: Record<string, unknown> }>; }
export interface McpRegistryResponse { ok: boolean; servers: McpServer[]; summary: Record<string, number>; policy: string; }
export interface ChatProvider { id: 'lobehub'; name: string; ready: boolean; url: string; authentication: string; detail: string; }
export interface ChatStatus { ok: boolean; ready: boolean; url: string; authentication: string; mcp_enabled_count: number; detail: string; providers?: ChatProvider[]; }
export interface SystemConfig { ok: boolean; compute_mode: 'auto' | 'cpu' | 'nvidia' | 'amd'; resolved_compute_mode: 'cpu' | 'nvidia' | 'amd'; available_modes: string[]; updated_at: string; updated_by: string; }
export interface InstallBatchItem { batch_id: string; service_id: string; ordinal: number; explicitly_selected: number; state: string; job_id: string; error_json?: string; started_at: string; completed_at: string; }
export interface InstallBatch { id: string; actor: string; state: 'queued' | 'running' | 'paused' | 'succeeded' | 'cancelled' | 'completed_with_failures' | 'resetting' | 'reset_failed' | 'reset'; current_ordinal: number; created_at: string; updated_at: string; error?: { code?: string; message?: string }; items: InstallBatchItem[]; }
export interface InstallBatchEvent { id: number; job_id: string; event: string; created_at: string; detail: string; }
export interface InstallBatchJob { id: string; state: string; step_id: string; detail: string; events: InstallBatchEvent[]; }
export interface InstallBatchResponse { ok: boolean; batch: InstallBatch | null; current_job?: InstallBatchJob | null; reset?: boolean; }
export interface CredentialHandoff { id: string; service_id: string; job_id: string; state: string; created_at: string; expires_at: string; login_url: string; }
export interface CredentialReveal extends CredentialHandoff { username: string; email: string; password: string; }
