"""Durable deactivation and demotion: deny locally at once, then revoke until done.

Deactivating someone, or taking away their administrator role, first records
an access hold keyed by their Authentik subject. Mu3Lab's own API honours the
hold on the next request, whatever Authentik's sessions still claim. Every
credential Mu3Lab issued is then revoked as a separate, retried task, so an
unavailable LiteLLM, chat or Authentik delays revocation without losing it.

Holds are Mu3Lab's record of intent, not a second identity source. They are
removed only when an administrator reactivates or promotes the person again.
Native app tokens (mobile app logins, app passwords) are outside this scope;
see the residual-risk table in the R07 implementation record.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from ctl.jobs import redact
from ctl.runtime import RuntimePaths
from ctl.store import db

Kind = Literal["deactivated", "demoted"]
TARGETS: dict[str, tuple[str, ...]] = {
    "deactivated": ("authentik_account", "authentik_sessions", "gateway", "voice_key", "chat"),
    "demoted": ("authentik_role", "gateway", "chat"),
}
TARGET_LABELS = {
    "authentik_account": "Authentik sign-in",
    "authentik_sessions": "open Authentik sessions and tokens",
    "authentik_role": "administrator group",
    "gateway": "chat tool access",
    "voice_key": "voice key",
    "chat": "chat assistants",
}
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
INTERVAL = 30
CHAT_LOCK = "chat-assistants"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _database(paths: RuntimePaths | None = None):
    return db.database(paths or RuntimePaths())


def available(paths: RuntimePaths | None = None) -> bool:
    return (paths or RuntimePaths()).state.is_dir()


def hold(subject: str, paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    """The current hold on this subject, if any. Never raises for a missing store."""
    if not subject or not available(paths):
        return None
    try:
        with db.connect(_database(paths)) as connection:
            row = connection.execute("SELECT * FROM access_holds WHERE subject=?", (subject,)).fetchone()
    except sqlite3.Error:
        # Unreadable state must not grant access the hold would have denied.
        return {"subject": subject, "kind": "deactivated", "username": "", "unreadable": True}
    return dict(row) if row else None


def holds(paths: RuntimePaths | None = None) -> dict[str, dict[str, Any]]:
    if not available(paths):
        return {}
    with db.connect(_database(paths)) as connection:
        rows = connection.execute("SELECT * FROM access_holds").fetchall()
        tasks = connection.execute("SELECT * FROM access_revocations ORDER BY target").fetchall()
    result = {row["subject"]: dict(row) | {"tasks": []} for row in rows}
    for task in tasks:
        if task["subject"] in result:
            result[task["subject"]]["tasks"].append(dict(task))
    return result


def record(subject: str, username: str, kind: Kind, actor: str, paths: RuntimePaths | None = None) -> None:
    """Deny at once and queue every revocation this change needs.

    A deactivation replaces a demotion hold (its tasks are a subset). A
    demotion never weakens an existing deactivation.
    """
    if not subject:
        raise ValueError("A verified subject is required to revoke access.")
    stamp = _now()
    with db.connect(_database(paths)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT kind FROM access_holds WHERE subject=?", (subject,)).fetchone()
        if current and current["kind"] == "deactivated" and kind == "demoted":
            return
        connection.execute(
            "INSERT INTO access_holds VALUES (?,?,?,?,?) ON CONFLICT(subject) DO UPDATE SET "
            "username=excluded.username, kind=excluded.kind, created_at=excluded.created_at, "
            "created_by=excluded.created_by",
            (subject, username, kind, stamp, actor),
        )
        for target in TARGETS[kind]:
            # Repeating a deactivation re-runs revocation; completed tasks start again.
            connection.execute(
                "INSERT INTO access_revocations (subject,target,state,created_at,updated_at) "
                "VALUES (?,?, 'pending', ?, ?) ON CONFLICT(subject,target) DO UPDATE SET "
                "state='pending', attempts=0, next_attempt_at=0, last_error='', updated_at=excluded.updated_at",
                (subject, target, stamp, stamp),
            )


def release(subject: str, kinds: tuple[Kind, ...], paths: RuntimePaths | None = None) -> None:
    """Remove a hold of the given kinds and its outstanding revocations."""
    if not subject or not available(paths):
        return
    marks = ",".join("?" for _ in kinds)
    with db.connect(_database(paths)) as connection:
        connection.execute(f"DELETE FROM access_holds WHERE subject=? AND kind IN ({marks})", (subject, *kinds))


def summary(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Browser-safe revocation progress for one person."""
    if not entry:
        return None
    pending = [task for task in entry.get("tasks", []) if task["state"] == "pending"]
    action = "Sign-in disabled" if entry["kind"] == "deactivated" else "Administrator access removed"
    if pending:
        detail = f"{action}; {len(pending)} access revocation{'s' if len(pending) != 1 else ''} pending."
        errors = [task["last_error"] for task in pending if task["last_error"]]
        if errors:
            detail += f" Last problem: {errors[0]}"
    else:
        detail = f"{action}; every Mu3Lab-issued credential is revoked."
    return {
        "kind": entry["kind"],
        "state": "pending" if pending else "complete",
        "pending": len(pending),
        "pending_targets": [TARGET_LABELS.get(task["target"], task["target"]) for task in pending],
        "detail": detail,
        "since": entry["created_at"],
    }


# --------------------------------------------------------------------------- revocation targets


def _authentik_user(subject: str, username: str):
    from ctl.integrations.authentik import Authentik

    client = Authentik.runtime()
    user = client.user(username) if username else None
    if user is not None and str(user.get("uid")) != subject:
        # The username now belongs to someone else; never touch their account.
        user = None
    return client, user


def _authentik_account(subject: str, username: str) -> None:
    client, user = _authentik_user(subject, username)
    if user and user.get("is_active"):
        client.update_user(user["pk"], is_active=False)


def _authentik_sessions(subject: str, username: str) -> None:
    client, user = _authentik_user(subject, username)
    if user:
        client.end_sessions(user["username"])


def _authentik_role(subject: str, username: str) -> None:
    from ctl.people import ADMIN_GROUP, SUPERUSER_GROUP

    client, user = _authentik_user(subject, username)
    if not user:
        return
    names = {str(group["name"]) for group in (user.get("groups_obj") or []) if isinstance(group, dict)}
    for group in sorted(names & {ADMIN_GROUP, SUPERUSER_GROUP}):
        client.remove_from_group(group, user["pk"])


def _gateway(subject: str, _username: str) -> None:
    from gateway_authority import Authority

    directory = RuntimePaths().projects / "mcp-gateway" / "authority"
    if (directory / "authority.db").is_file():
        Authority(directory, initialize=False).revoke_subject(subject)


def _voice_key(subject: str, _username: str) -> None:
    from ctl import voice_keys

    voice_keys.revoke(subject)


def _chat(subject: str, _username: str) -> None:
    """Detach every Mu3Lab assistant's tools; conversations and agents stay."""
    from ctl import chat_connections, resource_locks
    from ctl.integrations.lobehub import LobeHub
    from ctl.lobehub_ops import origin

    # Chat synchronization rewrites the same records; never interleave with it.
    with resource_locks.hold(CHAT_LOCK):
        entry = chat_connections.records().get(subject) or {}
        managed = entry.get("managed") or {}
        if not entry.get("key") or not managed:
            return
        with LobeHub(origin(), entry["key"]) as client:
            managed = client.ensure_assistants([], managed, installed=set(managed))
        chat_connections.save(subject, {**entry, "managed": managed, "error": ""})


HANDLERS: dict[str, Callable[[str, str], None]] = {
    "authentik_account": _authentik_account,
    "authentik_sessions": _authentik_sessions,
    "authentik_role": _authentik_role,
    "gateway": _gateway,
    "voice_key": _voice_key,
    "chat": _chat,
}


def run(
    log: Callable[[str], None],
    *,
    subject: str | None = None,
    targets: tuple[str, ...] | None = None,
    now: Callable[[], float] = time.time,
    paths: RuntimePaths | None = None,
) -> int:
    """Attempt every due revocation (or all of one subject's now); return how many remain pending."""
    if not available(paths):
        return 0
    with db.connect(_database(paths)) as connection:
        due = connection.execute(
            "SELECT r.subject, r.target, r.attempts, h.username FROM access_revocations r "
            "JOIN access_holds h ON h.subject = r.subject "
            "WHERE r.state='pending' AND (r.next_attempt_at <= ? OR ? IS NOT NULL) AND (? IS NULL OR r.subject=?) "
            "ORDER BY r.created_at",
            (now(), subject, subject, subject),
        ).fetchall()
    for task in due:
        if targets is not None and task["target"] not in targets:
            continue
        handler = HANDLERS.get(task["target"])
        try:
            if handler is None:
                raise ValueError("unknown revocation target")
            handler(task["subject"], task["username"])
        except Exception as exc:
            attempts = int(task["attempts"]) + 1
            delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** (attempts - 1))
            message = redact(str(exc))[:300] or type(exc).__name__
            _update(task, "pending", attempts, now() + delay, message, paths)
            log(f"Revoking {TARGET_LABELS.get(task['target'], task['target'])} deferred: {message}")
        else:
            _update(task, "done", int(task["attempts"]) + 1, 0, "", paths)
    with db.connect(_database(paths)) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM access_revocations WHERE state='pending' AND (? IS NULL OR subject=?)",
                (subject, subject),
            ).fetchone()[0]
        )


def _update(task, state: str, attempts: int, next_attempt: float, error: str, paths: RuntimePaths | None) -> None:
    with db.connect(_database(paths)) as connection:
        # The hold may have been released meanwhile; then the row is already gone.
        connection.execute(
            "UPDATE access_revocations SET state=?, attempts=?, next_attempt_at=?, last_error=?, updated_at=? "
            "WHERE subject=? AND target=?",
            (state, attempts, next_attempt, error, _now(), task["subject"], task["target"]),
        )


def start(stopping: threading.Event, log: Callable[[str], None]) -> threading.Thread:
    def loop() -> None:
        while not stopping.wait(timeout=INTERVAL):
            try:
                run(log)
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                logging.getLogger(__name__).warning("Access revocation deferred: %s", redact(str(exc)))

    thread = threading.Thread(target=loop, name="mu3lab-access-revocation", daemon=True)
    thread.start()
    return thread
