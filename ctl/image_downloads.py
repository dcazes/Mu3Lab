"""Live image-download progress for each app, read by the dashboard."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl.runtime import RuntimePaths

FIELDS = ("total_bytes", "done_bytes", "rate_bps", "images_total", "images_done", "connections")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ImageDownloadStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> ImageDownloadStore | None:
        return cls(paths.runtime / "control-plane.sqlite3") if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS image_downloads (
                service_id TEXT PRIMARY KEY, job_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL, total_bytes INTEGER NOT NULL DEFAULT 0,
                done_bytes INTEGER NOT NULL DEFAULT 0, rate_bps INTEGER NOT NULL DEFAULT 0,
                images_total INTEGER NOT NULL DEFAULT 0, images_done INTEGER NOT NULL DEFAULT 0,
                connections INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            )
        """)
        return conn

    def update(self, service_id: str, job_id: str, snapshot: dict[str, Any]) -> None:
        values = [int(snapshot.get(name, 0) or 0) for name in FIELDS]
        with self._connect() as conn:
            conn.execute(
                f"""INSERT INTO image_downloads (service_id, job_id, state, {", ".join(FIELDS)}, updated_at)
                    VALUES (?, ?, ?, {", ".join("?" for _ in FIELDS)}, ?)
                    ON CONFLICT(service_id) DO UPDATE SET job_id = excluded.job_id, state = excluded.state,
                    {", ".join(f"{name} = excluded.{name}" for name in FIELDS)}, updated_at = excluded.updated_at""",
                (service_id, job_id, str(snapshot.get("state", "downloading")), *values, _now()),
            )

    def for_jobs(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Progress keyed by job id, so a retried install never shows an old attempt."""
        wanted = [job_id for job_id in job_ids if job_id]
        if not wanted:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM image_downloads WHERE job_id IN ({', '.join('?' for _ in wanted)})", wanted
            ).fetchall()
        return {str(row["job_id"]): {key: row[key] for key in ("state", *FIELDS, "updated_at")} for row in rows}
