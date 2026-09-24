"""Durable, resumable first-run provisioning state.

This module is deliberately independent from the browser-facing bootstrap
server.  A browser refresh, control-plane restart, or logout must not erase
what Mu3Lab has already established about the host.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl.jobs import redact, redact_data
from ctl.runtime import RuntimePaths

WORKFLOW_VERSION = 3
PHASES = (
    ("foundation", "Host prerequisites and Docker", "Install the host runtime and private application networks."),
    ("vaultwarden", "Vaultwarden owner", "Verify the independent private password-vault owner account."),
    ("tailscale", "Tailscale and HTTPS routes", "Verify the tailnet connection and private HTTPS routes."),
    ("identity", "Authentik owner", "Create the Mu3Lab identity owner and operator mapping."),
    ("dashboard_protection", "Protected dashboard", "Prove that Authentik protects the dashboard."),
    ("core", "Core platform", "Start and configure the curated core services."),
    ("configuration", "Provider configuration", "Validate external inference access."),
    ("verification", "Verified handoff", "Prove that the user-facing platform works."),
)
VALID_STATES = frozenset({"pending", "running", "waiting_for_user", "verified", "failed", "skipped"})
TRANSITIONS = {
    "pending": frozenset({"running", "waiting_for_user", "verified", "failed", "skipped"}),
    "running": frozenset({"waiting_for_user", "verified", "failed", "pending"}),
    "waiting_for_user": frozenset({"running", "verified", "failed", "pending"}),
    "failed": frozenset({"running", "pending"}),
    "verified": frozenset({"running", "pending"}),
    "skipped": frozenset({"running", "pending"}),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ProvisioningStore:
    """A small, append-safe desired/actual-state record in the runtime DB."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> "ProvisioningStore | None":
        if not paths.runtime.is_dir():
            return None
        return cls(paths.runtime / "control-plane.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provisioning_steps (
                workflow_version INTEGER NOT NULL,
                phase_id TEXT NOT NULL,
                desired_state TEXT NOT NULL,
                actual_state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                inputs_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workflow_version, phase_id)
            )
        """)
        return conn

    def initialize(self) -> None:
        now = _now()
        with self._connect() as conn:
            for phase_id, _label, detail in PHASES:
                conn.execute("""
                    INSERT OR IGNORE INTO provisioning_steps
                    (workflow_version, phase_id, desired_state, actual_state, updated_at, detail)
                    VALUES (?, ?, 'verified', 'pending', ?, ?)
                """, (WORKFLOW_VERSION, phase_id, now, detail))
            for version, mappings in (
                (1, {"foundation": ("foundation", "vaultwarden", "tailscale"),
                     "identity": ("identity", "dashboard_protection"),
                     "configuration": ("configuration",)}),
                (2, {phase: (phase,) for phase in (
                    "foundation", "vaultwarden", "tailscale", "identity",
                    "dashboard_protection", "configuration")}),
            ):
                legacy = {str(row["phase_id"]): str(row["actual_state"])
                          for row in conn.execute("""SELECT phase_id, actual_state FROM provisioning_steps
                                                     WHERE workflow_version = ?""", (version,)).fetchall()}
                for old, targets in mappings.items():
                    if legacy.get(old) not in {"verified", "skipped"}:
                        continue
                    for target in targets:
                        conn.execute("""UPDATE provisioning_steps SET actual_state = 'verified',
                                        detail = ?, updated_at = ?
                                        WHERE workflow_version = ? AND phase_id = ?
                                          AND actual_state = 'pending'""",
                                     (f"Verified by the completed version {version} workflow.", now,
                                      WORKFLOW_VERSION, target))

    def update(self, phase_id: str, state: str, *, detail: str = "",
               error: str = "", inputs: dict[str, Any] | None = None) -> None:
        if phase_id not in {item[0] for item in PHASES}:
            raise ValueError("unknown provisioning phase")
        if state not in VALID_STATES:
            raise ValueError("invalid provisioning state")
        self.initialize()
        now = _now()
        payload = json.dumps(redact_data(inputs or {}), sort_keys=True)
        with self._connect() as conn:
            row = conn.execute("""
                SELECT actual_state FROM provisioning_steps
                WHERE workflow_version = ? AND phase_id = ?
            """, (WORKFLOW_VERSION, phase_id)).fetchone()
            current = str(row["actual_state"]) if row else "pending"
            if state != current and state not in TRANSITIONS[current]:
                raise ValueError(f"invalid provisioning transition: {current} -> {state}")
            conn.execute("""
                UPDATE provisioning_steps
                SET actual_state = ?, attempts = attempts + ?, detail = ?, error = ?,
                    inputs_json = ?, updated_at = ?
                WHERE workflow_version = ? AND phase_id = ?
            """, (state, 1 if state == "running" else 0, redact(detail), redact(error),
                  payload, now, WORKFLOW_VERSION, phase_id))

    def summary(self) -> dict[str, Any]:
        self.initialize()
        labels = {phase_id: (label, default_detail) for phase_id, label, default_detail in PHASES}
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT phase_id, desired_state, actual_state, attempts, detail, error,
                       updated_at FROM provisioning_steps
                WHERE workflow_version = ? ORDER BY rowid
            """, (WORKFLOW_VERSION,)).fetchall()
        phases = []
        for row in rows:
            value = dict(row)
            label, default_detail = labels[value["phase_id"]]
            phases.append({**value, "label": label,
                           "detail": value["detail"] or default_detail})
        completed = sum(item["actual_state"] in {"verified", "skipped"} for item in phases)
        verified = completed == len(phases)
        blocked = next((item for item in phases if item["actual_state"] == "failed"), None)
        waiting = next((item for item in phases if item["actual_state"] == "waiting_for_user"), None)
        incomplete = next((item for item in phases if item["actual_state"] not in {"verified", "skipped"}), None)
        if incomplete is None:
            next_action = {"kind": "none", "label": "Platform ready"}
        elif incomplete["phase_id"] in {"foundation", "vaultwarden", "tailscale", "identity", "dashboard_protection"}:
            next_action = {"kind": "bootstrap", "label": "Resume ./install"}
        elif incomplete["phase_id"] == "core":
            next_action = {"kind": "job", "label": "Install missing core services",
                           "endpoint": "/api/setup/core"}
        elif incomplete["phase_id"] == "configuration":
            next_action = {"kind": "link", "label": "Add or repair a provider",
                           "href": "/connections/providers"}
        else:
            next_action = {"kind": "job", "label": "Verify platform",
                           "endpoint": "/api/v1/jobs/core-verify"}
        return {"ok": True, "workflow_version": WORKFLOW_VERSION, "complete": verified,
                "blocked": blocked, "waiting": waiting, "phases": phases,
                "progress": {"completed": completed, "total": len(phases)},
                "next_action": next_action}

    def reconcile_runtime(self, paths: RuntimePaths = RuntimePaths()) -> None:
        """Advance durable milestones from local authoritative evidence."""
        self.initialize()
        current = {item["phase_id"]: item["actual_state"] for item in self.summary()["phases"]}
        try:
            from ctl.control_state import ControlState
            control = ControlState.runtime(paths)
            providers = control.providers() if control else []
            if any(item.get("enabled") and item.get("state") == "verified" for item in providers):
                if current.get("configuration") not in {"verified", "skipped"}:
                    self.update("configuration", "verified",
                                detail="An enabled inference provider passed streamed routing verification.")
            elif current.get("configuration") in {"pending", "running", "waiting_for_user"}:
                degraded = next((item for item in providers if item.get("state") == "degraded"), None)
                detail = ("A saved provider needs attention before chat can be verified." if degraded
                          else "Add and verify an inference provider to finish chat setup.")
                self.update("configuration", "waiting_for_user", detail=detail)
        except (OSError, sqlite3.Error, ValueError):
            pass
