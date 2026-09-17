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

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
