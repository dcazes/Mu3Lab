"""Persistent control-plane configuration and installation projections.

This module owns data that describes *what Mu3Lab believes is installed*.
Docker remains the runtime source of truth, while these records make jobs,
configuration, and recovery deterministic across dashboard/worker restarts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl.runtime import RuntimePaths

COMPUTE_MODES = frozenset({"auto", "cpu", "nvidia", "amd"})
INSTALL_STATES = frozenset({
    "not_installed", "config_required", "queued", "installing", "starting",
    "verifying", "running", "stopped", "degraded", "failed",
})
MCP_STATES = frozenset({
    "unavailable", "disabled", "starting", "live", "degraded",
    "authentication_required", "incompatible", "failed", "stopped",
})
INITIALIZATION_STATES = frozenset({
    "not_required", "pending", "initializing", "awaiting_user", "ready",
    "existing_account", "failed",
})
PROVIDER_STATES = frozenset({"saved", "verifying", "verified", "degraded", "disabled", "unsupported_legacy"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ControlState:
    """SQLite-backed settings, service installation, and MCP runtime state."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> "ControlState | None":
        if not paths.runtime.is_dir():
            return None
        return cls(paths.runtime / "control-plane.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_installations (
                service_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                manifest_version TEXT NOT NULL DEFAULT '',
                image_digests_json TEXT NOT NULL DEFAULT '{}',
                config_revision INTEGER NOT NULL DEFAULT 0,
                route_state TEXT NOT NULL DEFAULT 'unknown',
                last_job_id TEXT NOT NULL DEFAULT '',
                last_error_json TEXT NOT NULL DEFAULT '{}',
                installed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_servers (
                server_id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'disabled',
                tool_snapshot_json TEXT NOT NULL DEFAULT '[]',
                last_verified_at TEXT NOT NULL DEFAULT '',
                last_error_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_connections (
                provider_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL DEFAULT 'saved',
                model_samples_json TEXT NOT NULL DEFAULT '[]',
                last_attempt_at TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                last_error_json TEXT NOT NULL DEFAULT '{}',
                active_job_id TEXT NOT NULL DEFAULT '',
                config_revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_initializations (
                service_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                state TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                owner_uid TEXT NOT NULL DEFAULT '',
                credential_handoff_id TEXT NOT NULL DEFAULT '',
                last_error_json TEXT NOT NULL DEFAULT '{}',
                verified_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS credential_handoffs (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                owner_uid TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                confirmed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS credential_handoffs_owner
                ON credential_handoffs(owner_uid, state, expires_at);
        """)
        return conn

    def system_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json, updated_at, updated_by FROM system_config WHERE key = ?",
                ("compute_mode",),
            ).fetchone()
        if not row:
            return {"compute_mode": "auto", "updated_at": "", "updated_by": ""}
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            value = "auto"
        return {"compute_mode": value if value in COMPUTE_MODES else "auto",
                "updated_at": row["updated_at"], "updated_by": row["updated_by"]}

    def set_compute_mode(self, mode: str, actor: str) -> dict[str, Any]:
        if mode not in COMPUTE_MODES:
            raise ValueError("compute_mode must be auto, cpu, nvidia, or amd")
        if not actor:
            raise ValueError("actor is required")
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO system_config (key, value_json, updated_at, updated_by)
                VALUES ('compute_mode', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                    updated_at = excluded.updated_at, updated_by = excluded.updated_by
            """, (json.dumps(mode), now, actor))
        return {"compute_mode": mode, "updated_at": now, "updated_by": actor}

    def installation(self, service_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM service_installations WHERE service_id = ?", (service_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        for column in ("image_digests_json", "last_error_json"):
            key = column.removesuffix("_json")
            try:
                result[key] = json.loads(result.pop(column))
            except (TypeError, ValueError):
                result[key] = {}
        return result

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM provider_connections WHERE provider_id = ?", (provider_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        for column, fallback in (("model_samples_json", []), ("last_error_json", {})):
            key = column.removesuffix("_json")
            try:
                result[key] = json.loads(result.pop(column))
            except (TypeError, ValueError):
                result[key] = fallback
        return result

    def providers(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT provider_id FROM provider_connections ORDER BY updated_at DESC").fetchall()
        return [item for row in rows if (item := self.provider(str(row["provider_id"]))) is not None]

    def set_provider(self, provider_id: str, label: str, *, enabled: bool = True,
                     state: str = "saved", models: list[str] | None = None,
                     error: dict[str, Any] | None = None, attempted: bool = False,
                     verified: bool = False, job_id: str = "") -> dict[str, Any]:
        if not provider_id or state not in PROVIDER_STATES or not label:
            raise ValueError("invalid provider connection state")
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO provider_connections
                (provider_id, label, enabled, state, model_samples_json, last_attempt_at,
                 last_verified_at, last_error_json, active_job_id, config_revision, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(provider_id) DO UPDATE SET label = excluded.label,
                    enabled = excluded.enabled, state = excluded.state,
                    model_samples_json = CASE WHEN excluded.model_samples_json != '[]'
                        THEN excluded.model_samples_json ELSE provider_connections.model_samples_json END,
                    last_attempt_at = CASE WHEN excluded.last_attempt_at != ''
                        THEN excluded.last_attempt_at ELSE provider_connections.last_attempt_at END,
                    last_verified_at = CASE WHEN excluded.last_verified_at != ''
                        THEN excluded.last_verified_at ELSE provider_connections.last_verified_at END,
                    last_error_json = excluded.last_error_json,
                    active_job_id = excluded.active_job_id,
                    config_revision = provider_connections.config_revision + 1,
                    updated_at = excluded.updated_at
            """, (provider_id, label, int(enabled), state,
                  json.dumps(models or [], sort_keys=True), now if attempted else "",
                  now if verified else "", json.dumps(error or {}, sort_keys=True), job_id, now))
        return self.provider(provider_id) or {}

    def delete_provider(self, provider_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM provider_connections WHERE provider_id = ?", (provider_id,))

    def set_installation(self, service_id: str, state: str, *, job_id: str = "",
                         manifest_version: str = "", image_digests: dict[str, str] | None = None,
                         route_state: str = "unknown", error: dict[str, Any] | None = None) -> dict[str, Any]:
        if not service_id or state not in INSTALL_STATES:
            raise ValueError("invalid service installation state")
        now = _now()
        installed_at = now if state == "running" else ""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT installed_at, config_revision FROM service_installations WHERE service_id = ?",
                (service_id,),
            ).fetchone()
            if existing and existing["installed_at"]:
                installed_at = existing["installed_at"]
            conn.execute("""
                INSERT INTO service_installations
                (service_id, state, manifest_version, image_digests_json, config_revision,
                 route_state, last_job_id, last_error_json, installed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET state = excluded.state,
                    manifest_version = CASE WHEN excluded.manifest_version != ''
                        THEN excluded.manifest_version ELSE service_installations.manifest_version END,
                    image_digests_json = CASE WHEN excluded.image_digests_json != '{}'
                        THEN excluded.image_digests_json ELSE service_installations.image_digests_json END,
                    route_state = excluded.route_state, last_job_id = excluded.last_job_id,
                    last_error_json = excluded.last_error_json,
                    installed_at = CASE WHEN service_installations.installed_at != ''
                        THEN service_installations.installed_at ELSE excluded.installed_at END,
                    updated_at = excluded.updated_at
            """, (service_id, state, manifest_version,
                  json.dumps(image_digests or {}, sort_keys=True),
                  int(existing["config_revision"]) if existing else 0,
                  route_state, job_id, json.dumps(error or {}, sort_keys=True),
                  installed_at, now))
        return self.installation(service_id) or {}

    def initialization(self, service_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM service_initializations WHERE service_id = ?",
                               (service_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["last_error"] = json.loads(result.pop("last_error_json"))
        except (TypeError, ValueError):
            result["last_error"] = {}
        return result

    def set_initialization(self, service_id: str, mode: str, state: str, *,
                           job_id: str = "", owner_uid: str = "", handoff_id: str = "",
                           error: dict[str, Any] | None = None) -> dict[str, Any]:
        if not service_id or state not in INITIALIZATION_STATES:
            raise ValueError("invalid service initialization state")
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO service_initializations
                (service_id, mode, state, job_id, owner_uid, credential_handoff_id,
                 last_error_json, verified_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET mode = excluded.mode,
                    state = excluded.state, job_id = excluded.job_id,
                    owner_uid = CASE WHEN excluded.owner_uid != '' THEN excluded.owner_uid
                        ELSE service_initializations.owner_uid END,
                    credential_handoff_id = CASE WHEN excluded.credential_handoff_id != ''
                        THEN excluded.credential_handoff_id
                        ELSE service_initializations.credential_handoff_id END,
                    last_error_json = excluded.last_error_json,
                    verified_at = CASE WHEN excluded.verified_at != '' THEN excluded.verified_at
                        ELSE service_initializations.verified_at END,
                    updated_at = excluded.updated_at
            """, (service_id, mode, state, job_id, owner_uid, handoff_id,
                  json.dumps(error or {}, sort_keys=True), now if state == "ready" else "", now))
        return self.initialization(service_id) or {}

    def add_handoff(self, handoff_id: str, service_id: str, job_id: str,
                    owner_uid: str, created_at: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO credential_handoffs
                (id, service_id, job_id, owner_uid, state, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'available', ?, ?)
            """, (handoff_id, service_id, job_id, owner_uid, created_at, expires_at))

    def handoffs(self, owner_uid: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM credential_handoffs WHERE owner_uid = ?
                ORDER BY created_at DESC
            """, (owner_uid,)).fetchall()
        return [dict(row) for row in rows]

    def confirm_handoff(self, handoff_id: str, owner_uid: str) -> bool:
        now = _now()
        with self._connect() as conn:
            result = conn.execute("""
                UPDATE credential_handoffs SET state = 'confirmed', confirmed_at = ?
                WHERE id = ? AND owner_uid = ? AND state = 'available'
            """, (now, handoff_id, owner_uid))
        return result.rowcount == 1

    def expire_handoffs(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._connect() as conn:
            conn.executemany("UPDATE credential_handoffs SET state = 'expired' WHERE id = ?",
                             ((value,) for value in ids if value))

    def bump_config_revision(self, service_id: str) -> int:
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO service_installations (service_id, state, config_revision, updated_at)
                VALUES (?, 'config_required', 1, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    config_revision = service_installations.config_revision + 1,
                    updated_at = excluded.updated_at
            """, (service_id, now))
            row = conn.execute(
                "SELECT config_revision FROM service_installations WHERE service_id = ?",
                (service_id,),
            ).fetchone()
        return int(row[0])

    def mcp_server(self, server_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        for column in ("tool_snapshot_json", "last_error_json"):
            key = column.removesuffix("_json")
            try:
                result[key] = json.loads(result.pop(column))
            except (TypeError, ValueError):
                result[key] = [] if key == "tool_snapshot" else {}
        return result

    def set_mcp_server(self, server_id: str, service_id: str, *, enabled: bool,
                       state: str, tools: list[dict[str, Any]] | None = None,
                       error: dict[str, Any] | None = None, verified: bool = False) -> dict[str, Any]:
        if state not in MCP_STATES:
            raise ValueError("invalid MCP state")
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO mcp_servers
                (server_id, service_id, enabled, state, tool_snapshot_json,
                 last_verified_at, last_error_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET enabled = excluded.enabled,
                    state = excluded.state, tool_snapshot_json = excluded.tool_snapshot_json,
                    last_verified_at = CASE WHEN excluded.last_verified_at != ''
                        THEN excluded.last_verified_at ELSE mcp_servers.last_verified_at END,
                    last_error_json = excluded.last_error_json, updated_at = excluded.updated_at
            """, (server_id, service_id, int(enabled), state,
                  json.dumps(tools or [], sort_keys=True), now if verified else "",
                  json.dumps(error or {}, sort_keys=True), now))
        return self.mcp_server(server_id) or {}
