"""Persistent, dependency-ordered optional application install batches."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl import sqlite_store, workflow_secrets
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.registry import Registry, RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Apps download in parallel (up to this many at once, the owner can change
# it) and are set up one at a time as each download finishes, so a small app
# is usable while a large one is still downloading.
DEFAULT_PARALLEL_DOWNLOADS = 3
MAX_PARALLEL_DOWNLOADS = 8
# download_state: '' waiting, 'downloading', 'paused', or 'ready' (images are
# present, or the setup step will fall back to Docker's own pull).
DOWNLOAD_STATES = frozenset({"", "downloading", "paused", "ready"})


def _clamp_parallel(count: int) -> int:
    try:
        return max(1, min(MAX_PARALLEL_DOWNLOADS, int(count)))
    except (TypeError, ValueError):
        return DEFAULT_PARALLEL_DOWNLOADS


class InstallBatchStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> InstallBatchStore | None:
        return cls(paths.runtime / "control-plane.sqlite3") if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite_store.connect(self.database)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        with sqlite_store.schema_once(conn, self.database, "install_batches") as needed:
            if needed:
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
                    conn.execute(
                        "ALTER TABLE install_batch_items ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'"
                    )
                if "priority" not in columns:
                    conn.execute("ALTER TABLE install_batch_items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
                    conn.execute("UPDATE install_batch_items SET priority = ordinal")
                if "download_state" not in columns:
                    # Items of batches from before parallel downloads fetch their own images during setup.
                    conn.execute("ALTER TABLE install_batch_items ADD COLUMN download_state TEXT NOT NULL DEFAULT ''")
                    conn.execute("UPDATE install_batch_items SET download_state = 'ready'")
                if "download_error" not in columns:
                    conn.execute("ALTER TABLE install_batch_items ADD COLUMN download_error TEXT NOT NULL DEFAULT ''")
                batch_columns = {row[1] for row in conn.execute("PRAGMA table_info(install_batches)")}
                if "parallel_downloads" not in batch_columns:
                    conn.execute(
                        "ALTER TABLE install_batches ADD COLUMN parallel_downloads INTEGER NOT NULL "
                        f"DEFAULT {DEFAULT_PARALLEL_DOWNLOADS}"
                    )
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

    def _enqueue(
        self, batch_id: str, ordinal: int, actor: str, jobs: JobStore, *, force_new: bool = False
    ) -> dict[str, Any]:
        with self._connect() as conn:
            item = conn.execute(
                """
                SELECT service_id FROM install_batch_items
                WHERE batch_id = ? AND ordinal = ?
            """,
                (batch_id, ordinal),
            ).fetchone()
        if not item:
            raise ValueError("batch item not found")
        idempotency_key = (
            f"batch:{batch_id}:{ordinal}:retry:{uuid4().hex}" if force_new else f"batch:{batch_id}:{ordinal}"
        )
        job = jobs.create(
            kind="lifecycle",
            service_id=str(item["service_id"]),
            action="install",
            actor=actor,
            detail="Queued by a reviewed application install batch.",
            idempotency_key=idempotency_key,
        )
        identity = workflow_secrets.job_identity(f"batch:{batch_id}")
        if identity:
            workflow_secrets.save_job_identity(str(job["id"]), **identity)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE install_batch_items SET state = 'queued', job_id = ?, started_at = ?
                WHERE batch_id = ? AND ordinal = ?
            """,
                (job["id"], now, batch_id, ordinal),
            )
            conn.execute(
                """
                UPDATE install_batches SET state = 'running', current_ordinal = ?, updated_at = ?
                WHERE id = ?
            """,
                (ordinal, now, batch_id),
            )
        ControlState(self.database).set_installation(str(item["service_id"]), "queued", job_id=str(job["id"]))
        return job

    def create(
        self,
        registry: Registry,
        service_ids: list[str],
        *,
        actor: str,
        owner_uid: str,
        identity: workflow_secrets.JobIdentity,
        idempotency_key: str,
        jobs: JobStore,
        control: ControlState,
        parallel_downloads: int = DEFAULT_PARALLEL_DOWNLOADS,
    ) -> dict[str, Any]:
        """Record the plan; the worker's download manager then starts downloads,
        and each app is set up once its download is ready."""
        parallel_downloads = _clamp_parallel(parallel_downloads)
        with self._connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT id FROM install_batches WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return self.get(str(existing["id"])) or {}
        plan = self.plan(registry, service_ids, control)
        if not plan:
            raise ValueError("all selected applications are already installed")
        batch_id, now = uuid4().hex, _now()
        appended = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Keep one queue visible to its owner and append new selections behind
            # existing items. The transaction also prevents duplicate submissions.
            active = conn.execute(
                "SELECT id FROM install_batches WHERE owner_uid = ? AND state IN ('queued', 'running') "
                "ORDER BY created_at LIMIT 1",
                (owner_uid,),
            ).fetchone()
            ordinal_start = priority_start = 0
            if active:
                appended = True
                batch_id = str(active["id"])
                existing = conn.execute(
                    "SELECT service_id, ordinal, priority FROM install_batch_items WHERE batch_id = ?", (batch_id,)
                ).fetchall()
                present = {str(item["service_id"]) for item in existing}
                plan = [(slug, explicit) for slug, explicit in plan if slug not in present]
                if not plan:
                    raise ValueError("all selected applications are already queued")
                ordinal_start = max((int(item["ordinal"]) for item in existing), default=-1) + 1
                priority_start = max((int(item["priority"]) for item in existing), default=-1) + 1
            busy = {
                str(row[0])
                for row in conn.execute(
                    "SELECT i.service_id FROM install_batch_items i JOIN install_batches b ON b.id = i.batch_id "
                    "WHERE b.state IN ('queued', 'running', 'paused', 'resetting') "
                    "AND i.state IN ('pending', 'queued', 'running', 'resetting') AND b.id <> ?",
                    (batch_id,),
                )
            }
            if any(slug in busy for slug, _ in plan):
                raise ValueError("a selected application is already in another installation queue")
            if appended:
                conn.execute(
                    "UPDATE install_batches SET parallel_downloads = ?, updated_at = ? WHERE id = ?",
                    (parallel_downloads, now, batch_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO install_batches
                    (id, actor, owner_uid, state, idempotency_key, parallel_downloads, created_at, updated_at)
                    VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
                    """,
                    (batch_id, actor, owner_uid, idempotency_key or None, parallel_downloads, now, now),
                )
            conn.executemany(
                """
                INSERT INTO install_batch_items
                (batch_id, service_id, ordinal, explicitly_selected, state, depends_on_json, priority)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    (
                        batch_id,
                        service_id,
                        ordinal_start + index,
                        int(explicit),
                        json.dumps(list(registry.get(service_id).dependencies)),
                        priority_start + index,
                    )
                    for index, (service_id, explicit) in enumerate(plan)
                ),
            )
        if not appended:
            workflow_secrets.save_job_identity(f"batch:{batch_id}", **identity)
        return self.get(batch_id) or {}

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            batch = conn.execute("SELECT * FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
            items = conn.execute(
                """
                SELECT * FROM install_batch_items WHERE batch_id = ? ORDER BY priority, ordinal
            """,
                (batch_id,),
            ).fetchall()
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
            item = conn.execute(
                "SELECT service_id FROM install_batch_items WHERE batch_id = ? AND ordinal = ?", (batch_id, ordinal)
            ).fetchone()
        if not item:
            raise ValueError("batch reset item not found")
        job = jobs.create(
            kind="lifecycle",
            service_id=str(item["service_id"]),
            action="reset",
            actor=actor,
            detail="Queued cleanup for a failed application installation.",
            idempotency_key=f"batch-reset:{batch_id}:{ordinal}",
        )
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE install_batch_items SET state = 'resetting', job_id = ?,
                            started_at = ?, completed_at = '' WHERE batch_id = ? AND ordinal = ?""",
                (job["id"], now, batch_id, ordinal),
            )
            conn.execute(
                "UPDATE install_batches SET state = 'resetting', current_ordinal = ?, updated_at = ? WHERE id = ?",
                (ordinal, now, batch_id),
            )
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
                conn.execute(
                    """UPDATE install_batch_items SET state = ?, completed_at = ?
                                WHERE batch_id = ? AND ordinal = ?""",
                    (job["state"], now, batch_id, ordinal),
                )
            return
        if batch["state"] == "resetting":
            if job["state"] != "succeeded":
                error = {
                    "code": job.get("error_code") or "reset_cleanup_failed",
                    "message": job.get("detail") or "Application cleanup failed.",
                }
                with self._connect() as conn:
                    conn.execute(
                        """UPDATE install_batch_items SET state = 'reset_failed', error_json = ?, completed_at = ?
                                    WHERE batch_id = ? AND ordinal = ?""",
                        (json.dumps(error), now, batch_id, ordinal),
                    )
                    conn.execute(
                        "UPDATE install_batches SET state = 'reset_failed', error_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(error), now, batch_id),
                    )
                return
            with self._connect() as conn:
                conn.execute(
                    """UPDATE install_batch_items SET state = 'reset', completed_at = ?, error_json = '{}'
                                WHERE batch_id = ? AND ordinal = ?""",
                    (now, batch_id, ordinal),
                )
                next_item = conn.execute(
                    """SELECT ordinal FROM install_batch_items
                                            WHERE batch_id = ? AND state = 'reset_pending'
                                            ORDER BY ordinal LIMIT 1""",
                    (batch_id,),
                ).fetchone()
                actor = conn.execute("SELECT actor FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
                if not next_item:
                    conn.execute(
                        "UPDATE install_batches SET state = 'reset', error_json = '{}', updated_at = ? WHERE id = ?",
                        (now, batch_id),
                    )
                    workflow_secrets.delete_by_job(f"batch:{batch_id}")
                    return
            self._enqueue_reset(batch_id, int(next_item["ordinal"]), str(actor["actor"]), jobs)
            return
        if job["state"] != "succeeded":
            error = {
                "code": job.get("error_code") or "child_failed",
                "message": job.get("detail") or "Application installation failed.",
            }
            with self._connect() as conn:
                conn.execute(
                    """UPDATE install_batch_items SET state = ?, error_json = ?, completed_at = ?
                                WHERE batch_id = ? AND ordinal = ?""",
                    (job["state"], json.dumps(error), now, batch_id, ordinal),
                )
                pending = conn.execute(
                    """SELECT ordinal, service_id FROM install_batch_items
                                          WHERE batch_id = ? AND state = 'pending' ORDER BY ordinal""",
                    (batch_id,),
                ).fetchall()
                for candidate in pending:
                    if self._depends_on(str(candidate["service_id"]), str(item["service_id"])):
                        conn.execute(
                            """UPDATE install_batch_items SET state = 'blocked_by_dependency',
                                        error_json = ?, completed_at = ? WHERE batch_id = ? AND ordinal = ?""",
                            (
                                json.dumps(
                                    {
                                        "code": "blocked_by_dependency",
                                        "message": f"Blocked because {item['service_id']} failed.",
                                    }
                                ),
                                now,
                                batch_id,
                                candidate["ordinal"],
                            ),
                        )
                conn.execute(
                    "UPDATE install_batches SET error_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(error), now, batch_id),
                )
            self.continue_batch(batch_id, jobs)
            return
        with self._connect() as conn:
            conn.execute(
                """UPDATE install_batch_items SET state = 'succeeded', completed_at = ?
                            WHERE batch_id = ? AND ordinal = ?""",
                (now, batch_id, ordinal),
            )
        self.continue_batch(batch_id, jobs)

    # --- Parallel downloads, setup one at a time ------------------------------------

    @staticmethod
    def _ready_ordinal(conn: sqlite3.Connection, batch_id: str) -> int | None:
        """The first app, in the owner's order, whose download is ready and whose
        in-batch dependencies are installed."""
        states = {
            str(row["service_id"]): str(row["state"])
            for row in conn.execute("SELECT service_id, state FROM install_batch_items WHERE batch_id = ?", (batch_id,))
        }
        rows = conn.execute(
            """SELECT ordinal, depends_on_json FROM install_batch_items
               WHERE batch_id = ? AND state = 'pending' AND download_state = 'ready'
               ORDER BY priority, ordinal""",
            (batch_id,),
        ).fetchall()
        for row in rows:
            try:
                dependencies = [dep for dep in json.loads(row["depends_on_json"]) if dep in states]
            except (TypeError, ValueError):
                dependencies = []
            if all(states[dep] == "succeeded" for dep in dependencies):
                return int(row["ordinal"])
        return None

    def continue_batch(self, batch_id: str, jobs: JobStore) -> None:
        """Set up the next ready app, or finish the batch when nothing is left.

        Called when a setup job ends and when a download becomes ready; the
        claim happens under one write lock so two callers cannot both start a
        setup.
        """
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch = conn.execute("SELECT state, actor FROM install_batches WHERE id = ?", (batch_id,)).fetchone()
            if not batch or batch["state"] not in {"queued", "running"}:
                return
            active = conn.execute(
                "SELECT 1 FROM install_batch_items WHERE batch_id = ? AND state IN ('queued', 'running') LIMIT 1",
                (batch_id,),
            ).fetchone()
            if active:
                return
            ordinal = self._ready_ordinal(conn, batch_id)
            if ordinal is None:
                waiting = conn.execute(
                    "SELECT 1 FROM install_batch_items WHERE batch_id = ? AND state = 'pending' LIMIT 1", (batch_id,)
                ).fetchone()
                if waiting:
                    return  # still downloading (or paused)
                any_failure = conn.execute(
                    """SELECT 1 FROM install_batch_items WHERE batch_id = ?
                       AND state IN ('failed', 'cancelled', 'blocked_by_dependency') LIMIT 1""",
                    (batch_id,),
                ).fetchone()
                conn.execute(
                    "UPDATE install_batches SET state = ?, updated_at = ? WHERE id = ?",
                    ("completed_with_failures" if any_failure else "succeeded", now, batch_id),
                )
                finished = True
            else:
                conn.execute(
                    "UPDATE install_batch_items SET state = 'queued' WHERE batch_id = ? AND ordinal = ?",
                    (batch_id, ordinal),
                )
                finished = False
            actor = str(batch["actor"])
        if finished or ordinal is None:
            workflow_secrets.delete_by_job(f"batch:{batch_id}")
            return
        self._enqueue(batch_id, ordinal, actor, jobs)

    def download_queue(self) -> list[dict[str, Any]]:
        """Apps of running batches that still need their images, in the owner's order."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT i.batch_id, i.ordinal, i.service_id, i.download_state, i.priority, b.parallel_downloads
                FROM install_batch_items i JOIN install_batches b ON b.id = i.batch_id
                WHERE b.state IN ('queued', 'running') AND i.state = 'pending' AND i.download_state != 'ready'
                ORDER BY b.created_at, i.priority, i.ordinal
            """).fetchall()
        return [dict(row) for row in rows]

    def download_state(self, batch_id: str, ordinal: int) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT download_state FROM install_batch_items WHERE batch_id = ? AND ordinal = ?", (batch_id, ordinal)
            ).fetchone()
        return str(row["download_state"]) if row else ""

    def set_download_state(self, batch_id: str, ordinal: int, state: str, error: str = "") -> None:
        if state not in DOWNLOAD_STATES:
            raise ValueError("unknown download state")
        with self._connect() as conn:
            # A pause the owner asked for wins over a download that was already starting.
            conn.execute(
                """UPDATE install_batch_items SET download_state = ?, download_error = ?
                   WHERE batch_id = ? AND ordinal = ? AND state = 'pending'
                   AND NOT (download_state = 'paused' AND ? = 'downloading')""",
                (state, error[:500], batch_id, ordinal, state),
            )

    def _item_ordinal(self, batch_id: str, service_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ordinal FROM install_batch_items WHERE batch_id = ? AND service_id = ?", (batch_id, service_id)
            ).fetchone()
        if not row:
            raise ValueError("app is not part of this batch")
        return int(row["ordinal"])

    def pause_download(self, batch_id: str, service_id: str) -> dict[str, Any]:
        ordinal = self._item_ordinal(batch_id, service_id)
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE install_batch_items SET download_state = 'paused'
                   WHERE batch_id = ? AND ordinal = ? AND state = 'pending' AND download_state IN ('', 'downloading')""",
                (batch_id, ordinal),
            ).rowcount
        if not changed:
            raise ValueError("only a waiting or downloading app can be paused")
        return self.get(batch_id) or {}

    def resume_download(self, batch_id: str, service_id: str) -> dict[str, Any]:
        ordinal = self._item_ordinal(batch_id, service_id)
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE install_batch_items SET download_state = ''
                   WHERE batch_id = ? AND ordinal = ? AND state = 'pending' AND download_state = 'paused'""",
                (batch_id, ordinal),
            ).rowcount
        if not changed:
            raise ValueError("this app's download is not paused")
        return self.get(batch_id) or {}

    def reorder(self, batch_id: str, service_ids: list[str]) -> dict[str, Any]:
        """Apply the owner's order to apps not yet being set up; others keep their place."""
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("list each app once")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            known = {
                str(row["service_id"])
                for row in conn.execute("SELECT service_id FROM install_batch_items WHERE batch_id = ?", (batch_id,))
            }
            if not known:
                raise ValueError("batch not found")
            if set(service_ids) - known:
                raise ValueError("the order lists an app that is not in this batch")
            for position, service_id in enumerate(service_ids):
                conn.execute(
                    """UPDATE install_batch_items SET priority = ?
                       WHERE batch_id = ? AND service_id = ? AND state = 'pending'""",
                    (position, batch_id, service_id),
                )
        return self.get(batch_id) or {}

    def set_parallel_downloads(self, batch_id: str, count: int) -> dict[str, Any]:
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE install_batches SET parallel_downloads = ?, updated_at = ? WHERE id = ?",
                (_clamp_parallel(count), _now(), batch_id),
            ).rowcount
        if not changed:
            raise ValueError("batch not found")
        return self.get(batch_id) or {}

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
        # A download can finish while no setup is running; start its setup.
        with self._connect() as conn:
            waiting = [str(row["id"]) for row in conn.execute("SELECT id FROM install_batches WHERE state = 'running'")]
        for batch_id in waiting:
            self.continue_batch(batch_id, jobs)

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
            result = conn.execute(
                """
                UPDATE install_batches SET state = 'cancelled', updated_at = ?
                WHERE id = ? AND state IN ('queued', 'running', 'paused', 'completed_with_failures')
            """,
                (now, batch_id),
            )
            conn.execute(
                """UPDATE install_batch_items SET state = 'cancelled'
                            WHERE batch_id = ? AND state = 'pending'""",
                (batch_id,),
            )
        return result.rowcount == 1

    def latest(self, owner_uid: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id FROM install_batches WHERE owner_uid = ?
                                  AND state != 'reset'
                                  ORDER BY created_at DESC LIMIT 1""",
                (owner_uid,),
            ).fetchone()
        return self.get(str(row["id"])) if row else None

    def resume(self, batch_id: str, jobs: JobStore) -> dict[str, Any]:
        batch = self.get(batch_id)
        if not batch or batch["state"] != "paused":
            raise ValueError("batch is not paused")
        failed = next((item for item in batch["items"] if item["state"] in {"failed", "cancelled"}), None)
        if not failed or not failed["job_id"]:
            raise ValueError("batch has no retryable failed item")
        retry = jobs.retry(
            str(failed["job_id"]),
            actor=str(batch["actor"]),
            idempotency_key=f"batch:{batch_id}:{failed['ordinal']}:retry:{uuid4().hex}",
        )
        identity = workflow_secrets.job_identity(f"batch:{batch_id}")
        if identity:
            workflow_secrets.save_job_identity(str(retry["id"]), **identity)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE install_batch_items SET state = 'queued', job_id = ?,
                            error_json = '{}', started_at = ?, completed_at = ''
                            WHERE batch_id = ? AND ordinal = ?""",
                (retry["id"], now, batch_id, failed["ordinal"]),
            )
            conn.execute(
                """UPDATE install_batches SET state = 'running', error_json = '{}',
                            current_ordinal = ?, updated_at = ? WHERE id = ?""",
                (failed["ordinal"], now, batch_id),
            )
        return self.get(batch_id) or {}

    def begin_reset(self, batch_id: str, jobs: JobStore) -> dict[str, Any]:
        """Queue durable, non-destructive cleanup outside the HTTP request."""
        batch = self.get(batch_id)
        if not batch or batch["state"] not in {"paused", "cancelled", "completed_with_failures", "reset_failed"}:
            raise ValueError("batch is not resettable")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE install_batch_items
                            SET state = CASE
                                WHEN state IN ('failed', 'cancelled', 'reset_failed') THEN 'reset_pending'
                                WHEN state = 'succeeded' THEN state
                                ELSE 'reset' END,
                                job_id = CASE WHEN state IN ('failed', 'cancelled', 'reset_failed') THEN '' ELSE job_id END,
                                error_json = CASE WHEN state = 'succeeded' THEN error_json ELSE '{}' END,
                                completed_at = CASE WHEN state = 'succeeded' THEN completed_at ELSE ? END
                            WHERE batch_id = ?""",
                (now, batch_id),
            )
            conn.execute(
                "UPDATE install_batches SET state = 'resetting', error_json = '{}', updated_at = ? WHERE id = ?",
                (now, batch_id),
            )
            next_item = conn.execute(
                """SELECT ordinal FROM install_batch_items WHERE batch_id = ?
                                        AND state = 'reset_pending' ORDER BY ordinal LIMIT 1""",
                (batch_id,),
            ).fetchone()
        if next_item:
            self._enqueue_reset(batch_id, int(next_item["ordinal"]), str(batch["actor"]), jobs)
        else:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE install_batches SET state = 'reset', updated_at = ? WHERE id = ?", (_now(), batch_id)
                )
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
            conn.execute(
                """UPDATE install_batch_items
                            SET state = CASE WHEN state = 'succeeded' THEN state ELSE 'reset' END,
                                job_id = CASE WHEN state = 'succeeded' THEN job_id ELSE '' END,
                                error_json = CASE WHEN state = 'succeeded' THEN error_json ELSE '{}' END,
                                completed_at = CASE WHEN state = 'succeeded' THEN completed_at ELSE ? END
                            WHERE batch_id = ?""",
                (now, batch_id),
            )
            conn.execute(
                "UPDATE install_batches SET state = 'reset', error_json = '{}', updated_at = ? WHERE id = ?",
                (now, batch_id),
            )
        workflow_secrets.delete_by_job(f"batch:{batch_id}")
        return self.get(batch_id) or {}
