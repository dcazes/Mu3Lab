/**
 * Mu3Lab :: dashboard/src/api.ts
 * WHAT: Typed fetch wrapper + shapes mirroring ctl/app.py responses.
 * WHY:  One place where frontend/backend contracts meet; drift breaks here
 *       loudly at compile time instead of silently at runtime.
 * DEBUG: Network tab shows /api/health + /api/status on every load.
 */
export interface Health {
  ok: boolean;
  version: string;
}

export interface InfraStatus {
  version: string;
  components: { id: string; label: string; detail: string }[];
}

export interface Service {
  id: string;
  name: string;
  category: string;
  lifecycle: 'always_on' | 'shared' | 'optional';
  https_port: number;
  auth: 'oidc' | 'proxy' | 'local' | 'excluded';
  profiles: string[];
  dependencies: string[];
  availability: 'available' | 'blocked';
  blocked_reason: string;
  mcp: { exposed: boolean; risk: string };
  state: 'healthy' | 'blocked' | 'not_installed' | 'stopped_or_unhealthy';
  detail: string;
  url: string;
  compose_present: boolean;
}

export interface ServicesResponse {
  ok: boolean;
  version: string;
  tailnet_dns_name: string;
  runtime: Record<string, string>;
  services: Service[];
}

export interface IntegrationsResponse {
  ok: boolean;
  policy: string;
  integrations: { source: string; destination: string; kind: string }[];
}

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
