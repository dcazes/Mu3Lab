"""Watch a Mu3Lab update after the restart, and put the previous version back if it fails (R17).

The worker that applies an update is replaced by the update, so it cannot
check the result or undo it. Before restarting, it copies this file out of
the checkout and starts it as its own short-lived systemd unit, which
outlives the dashboard and worker restarting. This file uses only Python's
standard library, so it runs the same whatever version of Mu3Lab is on disk.

``verify``: wait up to a few minutes for the new dashboard to answer and both
services to stay up. If they do, record success. If not, roll back.

``rollback``: the update failed while being installed; roll back straight away.

Rolling back stops both services, returns the checkout and its Python
packages to the version that was running, puts the database copy back if the
new version had upgraded the database, rebuilds that version's dashboard and
services (``python -m ctl.self_update finish``, from the old code), and starts
it again. The result goes to ``state/self-update.json`` and the audit log.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULT = "self-update.json"
VERIFY_SECONDS = 300
# Answering this many checks in a row, 10 seconds apart, counts as started.
STEADY_CHECKS = 3

Runner = Callable[..., tuple[int, str]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run(argv: list[str], *, cwd: str | None = None, timeout: int = 1800, env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def schema(database: Path) -> int:
    try:
        # Closed at once: the file may be replaced next, and sqlite3's own "with" only commits.
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
            return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError):
        return 0


def started(plan: dict, runner: Runner, check: Callable[[str], bool], sleep: Callable[[float], None], clock) -> bool:
    """The new version answers and both services stay active, before the deadline."""
    deadline = clock() + plan.get("verify_seconds", VERIFY_SECONDS)
    steady = 0
    while clock() < deadline:
        if _alive(plan.get("old_worker_pid")):
            sleep(10)
            continue
        rc, _out = runner(["systemctl", "--user", "is-active", "--quiet", *plan["units"]], timeout=30)
        steady = steady + 1 if rc == 0 and check(plan["health_url"]) else 0
        if steady >= STEADY_CHECKS:
            return True
        sleep(10)
    return False


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _record(plan: dict, result: dict) -> None:
    target = Path(plan["state"]) / RESULT
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _audit(database: Path, job_id: str, event: str, detail: str, *, job_state: str = "") -> None:
    """Note the outcome, and settle the update's job if a restored database brought it back as running."""
    try:
        with closing(sqlite3.connect(database)) as conn, conn:
            if job_state:
                conn.execute(
                    "UPDATE jobs SET state = ?, detail = ?, updated_at = ?, lease_owner = '', lease_expires_at = '' "
                    "WHERE id = ?",
                    (job_state, detail, _now(), job_id),
                )
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id or None, "update-guard", event, _now(), detail),
            )
    except sqlite3.Error:
        pass  # the result file still says what happened


def checkpoint_safe(database: Path, checkpoint: Path, job_id: str) -> bool:
    """Refuse to rewind any state except this update's own progress.

    Stop the services before checking. Unknown schema/data changes fail closed:
    a failed downgrade must keep later accepted jobs and completed effects.
    """

    def quote(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
            conn.execute("ATTACH DATABASE ? AS checkpoint", (f"file:{checkpoint}?mode=ro",))
            tables = {}
            for schema_name in ("main", "checkpoint"):
                tables[schema_name] = {
                    row[0]: row[1]
                    for row in conn.execute(
                        f"SELECT name, sql FROM {schema_name}.sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_version'"
                    )
                }
            if set(tables["checkpoint"]) - set(tables["main"]):
                return False
            for table, definition in tables["main"].items():
                name = quote(table)
                if table not in tables["checkpoint"]:
                    if conn.execute(f"SELECT 1 FROM main.{name} LIMIT 1").fetchone():
                        return False
                    continue
                if definition != tables["checkpoint"][table]:
                    return False
                column = (
                    "id" if table == "jobs" else "job_id" if table in {"audit", "job_events", "job_processes"} else ""
                )
                where = f" WHERE {column} IS NOT ?" if column else ""
                args = (job_id,) if column else ()
                queries = [f"SELECT * FROM {schema_name}.{name}{where}" for schema_name in ("main", "checkpoint")]
                counts = [conn.execute(f"SELECT COUNT(*) FROM ({query})", args).fetchone()[0] for query in queries]
                if counts[0] != counts[1]:
                    return False
                if conn.execute(f"{queries[0]} EXCEPT {queries[1]} LIMIT 1", args + args).fetchone():
                    return False
            return True
    except (sqlite3.Error, OSError):
        return False


def _blocked(plan: dict, detail: str) -> dict[str, Any]:
    result = {"state": "needs_attention", "at": _now(), "detail": detail, **_summary(plan)}
    _audit(Path(plan["database"]), plan["job_id"], "platform.update_recovery_blocked", detail, job_state="failed")
    _record(plan, result)
    return result


def rollback(plan: dict, reason: str, runner: Runner, check, sleep, clock) -> dict[str, Any]:
    root, database = plan["root"], Path(plan["database"])
    problems: list[str] = []
    rc, _out = runner(["systemctl", "--user", "stop", *plan["units"]], timeout=120)
    if rc:
        return _blocked(
            plan, "The services could not be stopped. Code and database were kept; recovery needs an administrator."
        )
    copy = Path(plan["database_copy"])
    if not copy.is_file() or not schema(copy) or not schema(database):
        return _blocked(
            plan,
            "The database checkpoint could not be verified. Services remain stopped; both versions were kept for recovery.",
        )
    upgraded = schema(database) != schema(copy)
    if upgraded and not checkpoint_safe(database, copy, plan["job_id"]):
        return _blocked(
            plan,
            "The database changed after the update checkpoint. Restoring it could replay or lose work. "
            "Services remain stopped; the current database and checkpoint were kept for administrator recovery.",
        )
    if upgraded:
        # No later work or unknown schema changes can be discarded by this copy.
        for suffix in ("-wal", "-shm"):
            Path(f"{database}{suffix}").unlink(missing_ok=True)
        shutil.copy2(copy, database)
        os.chmod(database, 0o600)
    rc, out = runner(["git", "-C", root, "checkout", "--quiet", "--detach", plan["before"]], timeout=300)
    if rc:
        problems.append(f"the previous code could not be checked out ({out[-200:]})")
    uv = Path(root) / ".tools" / "bin" / "uv"
    rc, out = runner([str(uv), "sync", "--frozen", "--no-dev", "--quiet"], cwd=root, timeout=1800, env=plan.get("env"))
    if rc:
        problems.append(f"its Python packages could not be restored ({out[-200:]})")
    if not problems:
        python = str(Path(root) / ".venv" / "bin" / "python")
        rc, out = runner([python, "-m", "ctl.self_update", "finish"], cwd=root, timeout=4 * 3600, env=plan.get("env"))
        if rc:
            problems.append(f"its dashboard and services could not be rebuilt ({out[-200:]})")
    if not problems:
        rc, _out = runner(["systemctl", "--user", "start", *plan["units"]], timeout=120)
        if rc:
            problems.append("the previous services could not be started")
    back = not problems and started(plan, runner, check, sleep, clock)
    if back:
        detail = f"Mu3Lab {plan['target']} {reason}, so Mu3Lab went back to {plan['running']}."
        if upgraded:
            detail += " The database was put back as it was before the update."
    else:
        why = "; ".join(problems) or "the previous version did not start either"
        detail = (
            f"Mu3Lab {plan['target']} {reason}, and going back to {plan['running']} did not finish: {why}. "
            f"In a terminal, run: cd {root} && git checkout {plan['before']} && ./install.sh"
        )
    result = {"state": "rolled_back" if back else "needs_attention", "at": _now(), "detail": detail, **_summary(plan)}
    _audit(database, plan["job_id"], "platform.update_rolled_back", detail, job_state="failed")
    _record(plan, result)
    return result


def _summary(plan: dict) -> dict[str, str]:
    return {"from": plan["running"], "to": plan["target"], "job_id": plan["job_id"]}


def guard(plan: dict, mode: str, *, runner: Runner = run, check=answers, sleep=time.sleep, clock=time.monotonic):
    if mode == "rollback":
        return rollback(
            plan, f"could not be installed ({plan.get('reason', 'see the update job')})", runner, check, sleep, clock
        )
    if started(plan, runner, check, sleep, clock):
        detail = f"Mu3Lab {plan['target']} started and answers."
        result = {"state": "verified", "at": _now(), "detail": detail, **_summary(plan)}
        _audit(Path(plan["database"]), plan["job_id"], "platform.update_verified", detail)
        _record(plan, result)
        return result
    return rollback(plan, "did not start after the restart", runner, check, sleep, clock)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"verify", "rollback"}:
        print("usage: update_guard.py verify|rollback PLAN.json", file=sys.stderr)
        return 2
    plan = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    result = guard(plan, argv[0])
    print(json.dumps(result))
    return 0 if result["state"] in {"verified", "rolled_back"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
