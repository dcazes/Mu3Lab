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

from ctl import job_guard
from ctl.runtime import RuntimePaths
from ctl.store import db, owners

COMPUTE_MODES = frozenset({"auto", "cpu", "nvidia", "amd"})
INSTALL_STATES = frozenset(
    {
        "not_installed",
        "config_required",
        "queued",
        "installing",
        "starting",
        "verifying",
        "uninstalling",
        "running",
        "stopped",
        "degraded",
        "failed",
    }
)
MCP_STATES = frozenset(
    {
        "unavailable",
        "disabled",
        "prepared",
        "starting",
        "live",
        "degraded",
        "authentication_required",
        "incompatible",
        "failed",
        "stopped",
    }
)
INITIALIZATION_STATES = frozenset(
    {
        "not_required",
        "pending",
        "initializing",
        "awaiting_user",
        "ready",
        "existing_account",
        "failed",
    }
)
PROVIDER_STATES = frozenset(
    {"saved", "verifying", "verified", "degraded", "disabled", "removing", "unsupported_legacy"}
)
IDENTITY_MODES = frozenset({"native_oidc", "trusted_header", "proxy_gate", "local", "none"})
IDENTITY_STATES = frozenset(
    {
        "unconfigured",
        "configuring",
        "migration_required",
        "ready",
        "degraded",
        "unsupported",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RevisionConflict(ValueError):
    """The configuration changed since the caller read it."""

    def __init__(self, current: int) -> None:
        super().__init__("This configuration was changed by someone else; reload it and try again.")
        self.current = current


class ControlState:
    """SQLite-backed settings, service installation, and MCP runtime state."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> ControlState | None:
        if not paths.runtime.is_dir():
            return None
        return cls(db.database(paths))

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.database)

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
        return {
            "compute_mode": value if value in COMPUTE_MODES else "auto",
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    def set_compute_mode(self, mode: str, actor: str) -> dict[str, Any]:
        if mode not in COMPUTE_MODES:
            raise ValueError("compute_mode must be auto, cpu, nvidia, or amd")
        if not actor:
            raise ValueError("actor is required")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO system_config (key, value_json, updated_at, updated_by)
                VALUES ('compute_mode', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                    updated_at = excluded.updated_at, updated_by = excluded.updated_by
            """,
                (json.dumps(mode), now, actor),
            )
        return {"compute_mode": mode, "updated_at": now, "updated_by": actor}

    def vault_seeded(self) -> dict[str, Any]:
        """When the owner last saved Mu3Lab logins to Vaultwarden, if ever."""
        with self._connect() as conn:
            row = conn.execute("SELECT updated_at, updated_by FROM system_config WHERE key = 'vault_seeded'").fetchone()
        return {
            "seeded": bool(row),
            "seeded_at": row["updated_at"] if row else "",
            "seeded_by": row["updated_by"] if row else "",
        }

    def mark_vault_seeded(self, actor: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO system_config (key, value_json, updated_at, updated_by)
                VALUES ('vault_seeded', 'true', ?, ?)
                ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at, updated_by = excluded.updated_by
            """,
                (_now(), actor),
            )

    def calendar_connection(self, owner_uid: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM calendar_connections WHERE owner_uid = ?", (owner_uid,)).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["calendars"] = json.loads(result.pop("calendars_json"))
        except (TypeError, ValueError):
            result["calendars"] = []
        return result

    def set_calendar_connection(
        self,
        owner_uid: str,
        username_hint: str,
        calendars: list[dict[str, str]],
        selected_id: str,
        *,
        state: str = "connected",
        error: str = "",
        success: bool = False,
    ) -> dict[str, Any]:
        if not owner_uid or len(owner_uid) > 256 or not selected_id:
            raise ValueError("invalid calendar connection metadata")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO calendar_connections
                (owner_uid, username_hint, selected_calendar_id, calendars_json,
                 state, last_error, last_success_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_uid) DO UPDATE SET
                    username_hint = excluded.username_hint,
                    selected_calendar_id = excluded.selected_calendar_id,
                    calendars_json = excluded.calendars_json,
                    state = excluded.state,
                    last_error = excluded.last_error,
                    last_success_at = CASE WHEN excluded.last_success_at != ''
                        THEN excluded.last_success_at ELSE calendar_connections.last_success_at END,
                    updated_at = excluded.updated_at
            """,
                (
                    owner_uid,
                    username_hint,
                    selected_id,
                    json.dumps(calendars, sort_keys=True),
                    state,
                    error,
                    now if success else "",
                    now,
                ),
            )
        return self.calendar_connection(owner_uid) or {}

    def delete_calendar_connection(self, owner_uid: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM calendar_connections WHERE owner_uid = ?", (owner_uid,))

    def service_identity(self, service_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM service_identity_state WHERE service_id = ?", (service_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["owner_uid"] = owners.get(service_id, self.database).get("owner_uid", "")
        try:
            result["last_error"] = json.loads(result.pop("last_error_json"))
        except (TypeError, ValueError):
            result["last_error"] = {}
        return result

    def set_service_identity(
        self,
        service_id: str,
        mode: str,
        state: str,
        *,
        owner_uid: str = "",
        job_id: str = "",
        detail: str = "",
        error: dict[str, Any] | None = None,
        verified: bool = False,
    ) -> dict[str, Any]:
        job_guard.checkpoint()
        if not service_id or mode not in IDENTITY_MODES or state not in IDENTITY_STATES:
            raise ValueError("invalid service identity state")
        owners.remember(service_id, {"owner_uid": owner_uid}, self.database)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO service_identity_state
                (service_id, mode, state, last_job_id, detail,
                 last_error_json, last_verified_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    mode = excluded.mode, state = excluded.state,
                    last_job_id = excluded.last_job_id, detail = excluded.detail,
                    last_error_json = excluded.last_error_json,
                    last_verified_at = CASE WHEN excluded.last_verified_at != ''
                        THEN excluded.last_verified_at ELSE service_identity_state.last_verified_at END,
                    updated_at = excluded.updated_at
            """,
                (
                    service_id,
                    mode,
                    state,
                    job_id,
                    detail,
                    json.dumps(error or {}, sort_keys=True),
                    now if verified else "",
                    now,
                ),
            )
        return self.service_identity(service_id) or {}

    def installation(self, service_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM service_installations WHERE service_id = ?", (service_id,)).fetchone()
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

    def reset_service(self, service_id: str) -> None:
        """Forget a failed or uninstalled optional app without touching application data.

        Runtime containers/projects are cleaned by the service operation.  The
        control-plane projection is removed here so the next catalog refresh
        derives a fresh planned/not-installed state instead of preserving a
        stale failed or initialization record.
        """
        job_guard.checkpoint()
        if not service_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM service_installations WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM service_initializations WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM service_identity_state WHERE service_id = ?", (service_id,))

    def calendar_owner_uids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT owner_uid FROM calendar_connections").fetchall()
        return [str(row[0]) for row in rows]

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

    def set_provider(
        self,
        provider_id: str,
        label: str,
        *,
        enabled: bool = True,
        state: str = "saved",
        models: list[str] | None = None,
        error: dict[str, Any] | None = None,
        attempted: bool = False,
        verified: bool = False,
        job_id: str = "",
        replace_models: bool = False,
    ) -> dict[str, Any]:
        if not provider_id or state not in PROVIDER_STATES or not label:
            raise ValueError("invalid provider connection state")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_connections
                (provider_id, label, enabled, state, model_samples_json, last_attempt_at,
                 last_verified_at, last_error_json, active_job_id, config_revision, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(provider_id) DO UPDATE SET label = excluded.label,
                    enabled = excluded.enabled, state = excluded.state,
                    model_samples_json = CASE WHEN ? = 1
                        THEN excluded.model_samples_json
                        WHEN excluded.model_samples_json != '[]'
                        THEN excluded.model_samples_json ELSE provider_connections.model_samples_json END,
                    last_attempt_at = CASE WHEN excluded.last_attempt_at != ''
                        THEN excluded.last_attempt_at ELSE provider_connections.last_attempt_at END,
                    last_verified_at = CASE WHEN excluded.last_verified_at != ''
                        THEN excluded.last_verified_at ELSE provider_connections.last_verified_at END,
                    last_error_json = excluded.last_error_json,
                    active_job_id = excluded.active_job_id,
                    config_revision = provider_connections.config_revision + 1,
                    updated_at = excluded.updated_at
            """,
                (
                    provider_id,
                    label,
                    int(enabled),
                    state,
                    json.dumps(models or [], sort_keys=True),
                    now if attempted else "",
                    now if verified else "",
                    json.dumps(error or {}, sort_keys=True),
                    job_id,
                    now,
                    int(replace_models),
                ),
            )
        return self.provider(provider_id) or {}

    def delete_provider(self, provider_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM provider_connections WHERE provider_id = ?", (provider_id,))

    def set_installation(
        self,
        service_id: str,
        state: str,
        *,
        job_id: str = "",
        manifest_version: str = "",
        image_digests: dict[str, str] | None = None,
        route_state: str = "unknown",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_guard.checkpoint()
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
            conn.execute(
                """
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
            """,
                (
                    service_id,
                    state,
                    manifest_version,
                    json.dumps(image_digests or {}, sort_keys=True),
                    int(existing["config_revision"]) if existing else 0,
                    route_state,
                    job_id,
                    json.dumps(error or {}, sort_keys=True),
                    installed_at,
                    now,
                ),
            )
        return self.installation(service_id) or {}

    def initialization(self, service_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM service_initializations WHERE service_id = ?", (service_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["owner_uid"] = owners.get(service_id, self.database).get("owner_uid", "")
        try:
            result["last_error"] = json.loads(result.pop("last_error_json"))
        except (TypeError, ValueError):
            result["last_error"] = {}
        return result

    def set_initialization(
        self,
        service_id: str,
        mode: str,
        state: str,
        *,
        job_id: str = "",
        owner_uid: str = "",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_guard.checkpoint()
        if not service_id or state not in INITIALIZATION_STATES:
            raise ValueError("invalid service initialization state")
        owners.remember(service_id, {"owner_uid": owner_uid}, self.database)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO service_initializations
                (service_id, mode, state, job_id,
                 last_error_json, verified_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET mode = excluded.mode,
                    state = excluded.state, job_id = excluded.job_id,
                    last_error_json = excluded.last_error_json,
                    verified_at = CASE WHEN excluded.verified_at != '' THEN excluded.verified_at
                        ELSE service_initializations.verified_at END,
                    updated_at = excluded.updated_at
            """,
                (
                    service_id,
                    mode,
                    state,
                    job_id,
                    json.dumps(error or {}, sort_keys=True),
                    now if state == "ready" else "",
                    now,
                ),
            )
        return self.initialization(service_id) or {}

    def bump_config_revision(self, service_id: str, *, expected: int | None = None) -> int:
        """Count one configuration change; with ``expected``, only if nobody else changed it first."""
        job_guard.checkpoint()
        now = _now()
        with self._connect() as conn:
            if expected is not None:
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT config_revision FROM service_installations WHERE service_id = ?", (service_id,)
                ).fetchone()
                current = int(row[0]) if row else 0
                if current != expected:
                    raise RevisionConflict(current)
            conn.execute(
                """
                INSERT INTO service_installations (service_id, state, config_revision, updated_at)
                VALUES (?, 'config_required', 1, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    config_revision = service_installations.config_revision + 1,
                    updated_at = excluded.updated_at
            """,
                (service_id, now),
            )
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

    def set_mcp_server(
        self,
        server_id: str,
        service_id: str,
        *,
        enabled: bool,
        state: str,
        tools: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
        verified: bool = False,
    ) -> dict[str, Any]:
        job_guard.checkpoint()
        if state not in MCP_STATES:
            raise ValueError("invalid MCP state")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_servers
                (server_id, service_id, enabled, state, tool_snapshot_json,
                 last_verified_at, last_error_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET enabled = excluded.enabled,
                    state = excluded.state, tool_snapshot_json = excluded.tool_snapshot_json,
                    last_verified_at = CASE WHEN excluded.last_verified_at != ''
                        THEN excluded.last_verified_at ELSE mcp_servers.last_verified_at END,
                    last_error_json = excluded.last_error_json, updated_at = excluded.updated_at
            """,
                (
                    server_id,
                    service_id,
                    int(enabled),
                    state,
                    json.dumps(tools or [], sort_keys=True),
                    now if verified else "",
                    json.dumps(error or {}, sort_keys=True),
                    now,
                ),
            )
        return self.mcp_server(server_id) or {}
