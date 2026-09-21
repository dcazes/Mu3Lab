"""Persistent, dependency-ordered optional application install batches."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.registry import Registry, RegistryError, load as load_registry
from ctl.runtime import RuntimePaths
from ctl import workflow_secrets


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class InstallBatchStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> "InstallBatchStore | None":
        return cls(paths.runtime / "control-plane.sqlite3") if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS install_batches (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, owner_uid TEXT NOT NULL,
                state TEXT NOT NULL, current_ordinal INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT, error_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS install_batches_idempotency
                ON install_batches(idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS install_batch_items (
                batch_id TEXT NOT NULL, service_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                explicitly_selected INTEGER NOT NULL, state TEXT NOT NULL,
                depends_on_json TEXT NOT NULL DEFAULT '[]',
                job_id TEXT NOT NULL DEFAULT '', error_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(batch_id, ordinal),
                FOREIGN KEY(batch_id) REFERENCES install_batches(id)
            );
            CREATE INDEX IF NOT EXISTS install_batch_items_job ON install_batch_items(job_id);
        """)
        # Existing control planes already have batch rows.  The dependency
        # snapshot is additive so old paused batches remain readable.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(install_batch_items)")}
        if "depends_on_json" not in columns:
            conn.execute("ALTER TABLE install_batch_items ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'")
        return conn

    def plan(self, registry: Registry, requested: list[str], control: ControlState) -> list[tuple[str, bool]]:
        if not requested or len(requested) != len(set(requested)):
            raise ValueError("select one or more unique applications")
        requested_set = set(requested)
        selected: set[str] = set()
        visiting: set[str] = set()
        order: list[str] = []

        def visit(service_id: str) -> None:
            if service_id in selected:
                return
            if service_id in visiting:
                raise ValueError("application dependency cycle detected")
            try:
                service = registry.get(service_id)
            except RegistryError as exc:
                raise ValueError(str(exc)) from exc
            if service.is_blocked:
                raise ValueError(f"{service.name} is blocked: {service.blocked_reason}")
            if service.stage not in {"optional", "core", "foundation"}:
                raise ValueError(f"{service.name} is not installable")
            if service_id in requested_set and service.stage != "optional":
                raise ValueError(f"{service.name} is managed by the core platform")
            visiting.add(service_id)
            for dependency in service.dependencies:
                dep = registry.get(dependency)
                installed = control.installation(dependency)
                if dep.stage in {"core", "foundation"}:
                    if dep.stage == "core" and (not installed or installed["state"] not in {"running", "stopped"}):
                        from ctl.registry import ROOT
                        from ctl.service_state import status, tailnet_dns_name
                        live = status(dep, tailnet_dns_name(), ROOT)
                        if live["state"] not in {"ready", "running"}:
                            raise ValueError(f"Install and verify {dep.name} before {service.name}")
                    continue
                visit(dependency)
            visiting.remove(service_id)
            installed = control.installation(service_id)
            # Durable records can lag behind a successful container recovery.
            # Compose/health evidence prevents a healthy application from
            # appearing selectable merely because its last job failed.
            live_installed = False
            # Unit stores intentionally use disposable SQLite files and must
            # not inspect a developer's live Docker daemon.  The production
            # runtime store, on the other hand, reconciles stale workflow rows
            # against the actual service before offering installation.
            if self.database.resolve() == (RuntimePaths().runtime / "control-plane.sqlite3").resolve():
                try:
                    from ctl.registry import ROOT
                    from ctl.service_state import status, tailnet_dns_name
                    live = status(service, tailnet_dns_name(), ROOT)
                    live_installed = live["state"] in {"ready", "running", "starting", "stopped", "needs_setup"}
                except (OSError, ValueError):
                    pass
            if (not installed or installed["state"] not in {"running", "stopped"}) and not live_installed:
                selected.add(service_id)
                order.append(service_id)

        for service_id in requested:
            visit(service_id)
        return [(service_id, service_id in requested_set) for service_id in order]

    def _enqueue(self, batch_id: str, ordinal: int, actor: str, jobs: JobStore,
                 *, force_new: bool = False) -> dict[str, Any]:
        with self._connect() as conn:
            item = conn.execute("""
                SELECT service_id FROM install_batch_items
                WHERE batch_id = ? AND ordinal = ?
            """, (batch_id, ordinal)).fetchone()
        if not item:
            raise ValueError("batch item not found")
        idempotency_key = (f"batch:{batch_id}:{ordinal}:retry:{uuid4().hex}"
                           if force_new else f"batch:{batch_id}:{ordinal}")
        job = jobs.create(kind="lifecycle", service_id=str(item["service_id"]), action="install",
                          actor=actor, detail="Queued by a reviewed application install batch.",
                          idempotency_key=idempotency_key)
        identity = workflow_secrets.job_identity(f"batch:{batch_id}")
        if identity:
            workflow_secrets.save_job_identity(str(job["id"]), **identity)
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE install_batch_items SET state = 'queued', job_id = ?, started_at = ?
                WHERE batch_id = ? AND ordinal = ?
            """, (job["id"], now, batch_id, ordinal))
            conn.execute("""
                UPDATE install_batches SET state = 'running', current_ordinal = ?, updated_at = ?
                WHERE id = ?
            """, (ordinal, now, batch_id))
        ControlState(self.database).set_installation(
            str(item["service_id"]), "queued", job_id=str(job["id"]))
        return job

    def create(self, registry: Registry, service_ids: list[str], *, actor: str,
               owner_uid: str, identity: dict[str, str], idempotency_key: str,
               jobs: JobStore, control: ControlState) -> dict[str, Any]:
        with self._connect() as conn:
            if idempotency_key:
                existing = conn.execute("SELECT id FROM install_batches WHERE idempotency_key = ?",
                                        (idempotency_key,)).fetchone()
                if existing:
                    return self.get(str(existing["id"])) or {}
        plan = self.plan(registry, service_ids, control)
        if not plan:
            raise ValueError("all selected applications are already installed")
        batch_id, now = uuid4().hex, _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT INTO install_batches
                (id, actor, owner_uid, state, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?)
            """, (batch_id, actor, owner_uid, idempotency_key or None, now, now))
            conn.executemany("""
                INSERT INTO install_batch_items
                (batch_id, service_id, ordinal, explicitly_selected, state, depends_on_json)
                VALUES (?, ?, ?, ?, 'pending', ?)
            """, ((batch_id, service_id, ordinal, int(explicit), json.dumps(list(registry.get(service_id).dependencies)))
                  for ordinal, (service_id, explicit) in enumerate(plan)))
        workflow_secrets.save_job_identity(f"batch:{batch_id}", **identity)
        self._enqueue(batch_id, 0, actor, jobs)
        return self.get(batch_id) or {}

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            batch = conn.execute("SELECT * FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
            items = conn.execute("""
                SELECT * FROM install_batch_items WHERE batch_id = ? ORDER BY ordinal
            """, (batch_id,)).fetchall()
        if not batch:
            return None
        result = dict(batch)
        try:
            result["error"] = json.loads(result.pop("error_json"))
        except (TypeError, ValueError):
            result["error"] = {}
        result["items"] = []
        for item in items:
            public = dict(item)
            try:
                public["depends_on"] = json.loads(public.pop("depends_on_json"))
            except (TypeError, ValueError):
                public["depends_on"] = []
            result["items"].append(public)
        return result

    @staticmethod
    def _depends_on(service_id: str, failed_id: str) -> bool:
        """Return whether a curated optional service transitively needs another.

        The saved snapshot handles historic batches, while the registry check
        also protects a newly-added dependency during a long-running batch.
        """
        try:
            registry = load_registry()
            seen: set[str] = set()

            def visit(candidate: str) -> bool:
                if candidate in seen:
                    return False
                seen.add(candidate)
                service = registry.get(candidate)
                return failed_id in service.dependencies or any(visit(dep) for dep in service.dependencies)

            return visit(service_id)
        except (RegistryError, OSError, ValueError):
            return False

    def _enqueue_reset(self, batch_id: str, ordinal: int, actor: str, jobs: JobStore) -> dict[str, Any]:
        with self._connect() as conn:
            item = conn.execute("SELECT service_id FROM install_batch_items WHERE batch_id = ? AND ordinal = ?",
                                (batch_id, ordinal)).fetchone()
        if not item:
            raise ValueError("batch reset item not found")
        job = jobs.create(kind="lifecycle", service_id=str(item["service_id"]), action="reset",
                          actor=actor, detail="Queued cleanup for a failed application installation.",
                          idempotency_key=f"batch-reset:{batch_id}:{ordinal}")
        now = _now()
        with self._connect() as conn:
            conn.execute("""UPDATE install_batch_items SET state = 'resetting', job_id = ?,
                            started_at = ?, completed_at = '' WHERE batch_id = ? AND ordinal = ?""",
                         (job["id"], now, batch_id, ordinal))
            conn.execute("UPDATE install_batches SET state = 'resetting', current_ordinal = ?, updated_at = ? WHERE id = ?",
                         (ordinal, now, batch_id))
        return job

    def advance_for_job(self, job_id: str, jobs: JobStore) -> None:
        job = jobs.get(job_id)
        if not job or job["state"] not in {"succeeded", "failed", "cancelled"}:
            return
        with self._connect() as conn:
            item = conn.execute("SELECT * FROM install_batch_items WHERE job_id = ?", (job_id,)).fetchone()
        if not item:
            return
        if item["state"] in {"succeeded", "cancelled", "reset"}:
            return
        batch_id, ordinal, now = str(item["batch_id"]), int(item["ordinal"]), _now()
        batch = self.get(batch_id)
        if not batch or batch["state"] == "cancelled":
            with self._connect() as conn:
                conn.execute("""UPDATE install_batch_items SET state = ?, completed_at = ?
                                WHERE batch_id = ? AND ordinal = ?""",
                             (job["state"], now, batch_id, ordinal))
            return
        if batch["state"] == "resetting":
            if job["state"] != "succeeded":
                error = {"code": job.get("error_code") or "reset_cleanup_failed",
                         "message": job.get("detail") or "Application cleanup failed."}
                with self._connect() as conn:
                    conn.execute("""UPDATE install_batch_items SET state = 'reset_failed', error_json = ?, completed_at = ?
                                    WHERE batch_id = ? AND ordinal = ?""",
                                 (json.dumps(error), now, batch_id, ordinal))
                    conn.execute("UPDATE install_batches SET state = 'reset_failed', error_json = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(error), now, batch_id))
                return
            with self._connect() as conn:
                conn.execute("""UPDATE install_batch_items SET state = 'reset', completed_at = ?, error_json = '{}'
                                WHERE batch_id = ? AND ordinal = ?""", (now, batch_id, ordinal))
                next_item = conn.execute("""SELECT ordinal FROM install_batch_items
                                            WHERE batch_id = ? AND state = 'reset_pending'
                                            ORDER BY ordinal LIMIT 1""", (batch_id,)).fetchone()
                actor = conn.execute("SELECT actor FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
                if not next_item:
                    conn.execute("UPDATE install_batches SET state = 'reset', error_json = '{}', updated_at = ? WHERE id = ?",
                                 (now, batch_id))
                    workflow_secrets.delete_by_job(f"batch:{batch_id}")
                    return
            self._enqueue_reset(batch_id, int(next_item["ordinal"]), str(actor["actor"]), jobs)
            return
        if job["state"] != "succeeded":
            error = {"code": job.get("error_code") or "child_failed",
                     "message": job.get("detail") or "Application installation failed."}
            with self._connect() as conn:
                conn.execute("""UPDATE install_batch_items SET state = ?, error_json = ?, completed_at = ?
                                WHERE batch_id = ? AND ordinal = ?""",
                             (job["state"], json.dumps(error), now, batch_id, ordinal))
                pending = conn.execute("""SELECT ordinal, service_id FROM install_batch_items
                                          WHERE batch_id = ? AND state = 'pending' ORDER BY ordinal""",
                                       (batch_id,)).fetchall()
                for candidate in pending:
                    if self._depends_on(str(candidate["service_id"]), str(item["service_id"])):
                        conn.execute("""UPDATE install_batch_items SET state = 'blocked_by_dependency',
                                        error_json = ?, completed_at = ? WHERE batch_id = ? AND ordinal = ?""",
                                     (json.dumps({"code": "blocked_by_dependency",
                                                  "message": f"Blocked because {item['service_id']} failed."}),
                                      now, batch_id, candidate["ordinal"]))
                next_item = conn.execute("""SELECT ordinal FROM install_batch_items
                                            WHERE batch_id = ? AND state = 'pending' ORDER BY ordinal LIMIT 1""",
                                         (batch_id,)).fetchone()
                actor = conn.execute("SELECT actor FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
                if not next_item:
                    conn.execute("""UPDATE install_batches SET state = 'completed_with_failures', error_json = ?,
                                    updated_at = ? WHERE id = ?""", (json.dumps(error), now, batch_id))
                    workflow_secrets.delete_by_job(f"batch:{batch_id}")
                    return
                conn.execute("UPDATE install_batches SET state = 'running', error_json = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(error), now, batch_id))
            self._enqueue(batch_id, int(next_item["ordinal"]), str(actor["actor"]), jobs)
            return
        with self._connect() as conn:
            conn.execute("""UPDATE install_batch_items SET state = 'succeeded', completed_at = ?
                            WHERE batch_id = ? AND ordinal = ?""", (now, batch_id, ordinal))
            next_item = conn.execute("""SELECT ordinal FROM install_batch_items
                                        WHERE batch_id = ? AND ordinal > ? AND state = 'pending'
                                        ORDER BY ordinal LIMIT 1""", (batch_id, ordinal)).fetchone()
            if not next_item:
                any_failure = conn.execute("""SELECT 1 FROM install_batch_items WHERE batch_id = ?
                                              AND state IN ('failed', 'cancelled', 'blocked_by_dependency') LIMIT 1""",
                                           (batch_id,)).fetchone()
                conn.execute("UPDATE install_batches SET state = ?, updated_at = ? WHERE id = ?",
                             ("completed_with_failures" if any_failure else "succeeded", now, batch_id))
                workflow_secrets.delete_by_job(f"batch:{batch_id}")
                return
            actor = conn.execute("SELECT actor FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
        self._enqueue(batch_id, int(next_item["ordinal"]), str(actor["actor"]), jobs)

    def reconcile(self, jobs: JobStore) -> None:
        """Advance terminal children left between job commit and worker shutdown."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT i.job_id FROM install_batch_items i
                JOIN install_batches b ON b.id = i.batch_id
                WHERE b.state IN ('running', 'resetting') AND i.state IN ('queued', 'running', 'resetting') AND i.job_id != ''
            """).fetchall()
        for row in rows:
            job = jobs.get(str(row["job_id"]))
            if job and job["state"] in {"succeeded", "failed", "cancelled"}:
                self.advance_for_job(str(row["job_id"]), jobs)

    def cancel(self, batch_id: str, jobs: JobStore) -> bool:
        now = _now()
        batch = self.get(batch_id)
        if not batch:
            return False
        for item in batch["items"]:
            if item["state"] == "queued" and item["job_id"]:
                job = jobs.get(str(item["job_id"]))
                if job and job["state"] == "queued":
                    try:
                        jobs.cancel(str(item["job_id"]), actor=str(batch["actor"]))
                    except (KeyError, ValueError):
                        pass
        with self._connect() as conn:
            result = conn.execute("""
                UPDATE install_batches SET state = 'cancelled', updated_at = ?
                WHERE id = ? AND state IN ('queued', 'running', 'paused', 'completed_with_failures')
            """, (now, batch_id))
            conn.execute("""UPDATE install_batch_items SET state = 'cancelled'
                            WHERE batch_id = ? AND state = 'pending'""", (batch_id,))
        return result.rowcount == 1

    def latest(self, owner_uid: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("""SELECT id FROM install_batches WHERE owner_uid = ?
                                  AND state != 'reset'
                                  ORDER BY created_at DESC LIMIT 1""", (owner_uid,)).fetchone()
        return self.get(str(row["id"])) if row else None

    def resume(self, batch_id: str, jobs: JobStore) -> dict[str, Any]:
        batch = self.get(batch_id)
        if not batch or batch["state"] != "paused":
            raise ValueError("batch is not paused")
        failed = next((item for item in batch["items"] if item["state"] in {"failed", "cancelled"}), None)
        if not failed or not failed["job_id"]:
            raise ValueError("batch has no retryable failed item")
        retry = jobs.retry(str(failed["job_id"]), actor=str(batch["actor"]),
                           idempotency_key=f"batch:{batch_id}:{failed['ordinal']}:retry:{uuid4().hex}")
        identity = workflow_secrets.job_identity(f"batch:{batch_id}")
        if identity:
            workflow_secrets.save_job_identity(str(retry["id"]), **identity)
        now = _now()
        with self._connect() as conn:
            conn.execute("""UPDATE install_batch_items SET state = 'queued', job_id = ?,
                            error_json = '{}', started_at = ?, completed_at = ''
                            WHERE batch_id = ? AND ordinal = ?""",
                         (retry["id"], now, batch_id, failed["ordinal"]))
            conn.execute("""UPDATE install_batches SET state = 'running', error_json = '{}',
                            current_ordinal = ?, updated_at = ? WHERE id = ?""",
                         (failed["ordinal"], now, batch_id))
        return self.get(batch_id) or {}

    def begin_reset(self, batch_id: str, jobs: JobStore) -> dict[str, Any]:
        """Queue durable, non-destructive cleanup outside the HTTP request."""
        batch = self.get(batch_id)
        if not batch or batch["state"] not in {"paused", "cancelled", "completed_with_failures", "reset_failed"}:
            raise ValueError("batch is not resettable")
        now = _now()
        with self._connect() as conn:
            conn.execute("""UPDATE install_batch_items
                            SET state = CASE
                                WHEN state IN ('failed', 'cancelled', 'reset_failed') THEN 'reset_pending'
                                WHEN state = 'succeeded' THEN state
                                ELSE 'reset' END,
                                job_id = CASE WHEN state IN ('failed', 'cancelled', 'reset_failed') THEN '' ELSE job_id END,
                                error_json = CASE WHEN state = 'succeeded' THEN error_json ELSE '{}' END,
                                completed_at = CASE WHEN state = 'succeeded' THEN completed_at ELSE ? END
                            WHERE batch_id = ?""", (now, batch_id))
            conn.execute("UPDATE install_batches SET state = 'resetting', error_json = '{}', updated_at = ? WHERE id = ?",
                         (now, batch_id))
            next_item = conn.execute("""SELECT ordinal FROM install_batch_items WHERE batch_id = ?
                                        AND state = 'reset_pending' ORDER BY ordinal LIMIT 1""",
                                     (batch_id,)).fetchone()
        if next_item:
            self._enqueue_reset(batch_id, int(next_item["ordinal"]), str(batch["actor"]), jobs)
        else:
            with self._connect() as conn:
                conn.execute("UPDATE install_batches SET state = 'reset', updated_at = ? WHERE id = ?", (_now(), batch_id))
            workflow_secrets.delete_by_job(f"batch:{batch_id}")
        return self.get(batch_id) or {}

    def reset(self, batch_id: str, jobs: JobStore) -> dict[str, Any]:
        """Compatibility projection for older callers that already cleaned up.

        Public API callers must use :meth:`begin_reset` so Docker work is
        durable and asynchronous.  This keeps historical tests and upgrade
        callers from accidentally re-queuing installation work after they
        have completed their own cleanup.
        """
        batch = self.get(batch_id)
        if not batch or batch["state"] not in {"paused", "cancelled", "completed_with_failures"}:
            raise ValueError("batch is not resettable")
        now = _now()
        with self._connect() as conn:
            conn.execute("""UPDATE install_batch_items
                            SET state = CASE WHEN state = 'succeeded' THEN state ELSE 'reset' END,
                                job_id = CASE WHEN state = 'succeeded' THEN job_id ELSE '' END,
                                error_json = CASE WHEN state = 'succeeded' THEN error_json ELSE '{}' END,
                                completed_at = CASE WHEN state = 'succeeded' THEN completed_at ELSE ? END
                            WHERE batch_id = ?""", (now, batch_id))
            conn.execute("UPDATE install_batches SET state = 'reset', error_json = '{}', updated_at = ? WHERE id = ?",
                         (now, batch_id))
        workflow_secrets.delete_by_job(f"batch:{batch_id}")
        return self.get(batch_id) or {}
