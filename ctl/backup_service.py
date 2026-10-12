"""Kopia's own server makes Mu3Lab's backups: the schedule, retention, maintenance and the browser.

One system does the whole job. The Kopia server (the ``kopia-ui`` app, which
administrators also open to browse backups) owns:

- **when** backups run: its global policy carries the household's schedule as
  a crontab, in this computer's time zone, and catches up on a run the
  computer was off for;
- **which** backups to keep: its retention policy (``backups.RETENTION``);
- reclaiming the space of deleted backups (its automatic maintenance).

Each backup source is ``/data/<app>``, with the app's data folders mounted
read-only inside it, plus ``/data/mu3lab`` for Mu3Lab's own state. Kopia
backs up one source at a time and runs two actions around each:

- **before**: ``POST /api/v1/backup-hooks/before-snapshot-root``. Mu3Lab notes
  the app's release, holds the app (nothing else may change it) and has the
  worker stop it; for ``mu3lab`` it writes the consistent copy of its own
  state. If this fails, Kopia skips that backup (the actions are
  ``essential``), so a running database is never copied.
- **after**: ``POST /api/v1/backup-hooks/after-snapshot-root``. The worker
  starts the app again with the installer's settings
  (``ctl.lifecycle.app_control``).

The worker keeps the server in step (``reconcile``, about once a minute): it
mounts each installed app's folders, sets the actions on each source, writes
the schedule and retention into the global policy, records each source's
newest backup, and starts any app a backup left stopped because Kopia never
ran the after-action. A bind mount follows the folder, not its name, so the
server is recreated, while idle, after a restore swaps an app's folders; until
then, the before-action refuses that app.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ctl import actions, backups, platform_backup
from ctl.control_state import ControlState
from ctl.jobs import JobConflict, JobStore
from ctl.lifecycle import app_control, app_releases, in_progress
from ctl.lifecycle.uninstall import backed_up, data_directories
from ctl.registry import RegistryError, load
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.secrets import read_runtime_env
from ctl.store import records

CAPABILITY = "backup_service"
CONTAINER = "kopia"
ADDRESS = "http://127.0.0.1:51515"
CONTROL_USER = "mu3lab-control"
HOOK = "/hooks/backup-hook.sh"
SOURCES_FILE = "docker-compose.sources.yml"
ACTION_TIMEOUT = "30m"
# How long the before- and after-actions wait for the worker; inside Kopia's action timeout.
HOOK_WAIT_SECONDS = 25 * 60
ACTOR = "backup-service"
STATE = ("backup", "service")
REACHABLE = ("backup", "service-reachable")  # since when the server has not answered
HOLDS = "backup-holds"
RESULTS = "backup-results"
# A held app whose source Kopia no longer backs up is started again after this long.
STUCK = timedelta(minutes=3)
# ...or, while Kopia cannot be asked, after this long.
UNREACHABLE = timedelta(minutes=45)

FREQUENCIES: tuple[str, ...] = ("off", "6h", "12h", "daily", "weekly")
_EVERY = {"6h": 6, "12h": 12, "daily": 24}
DEFAULTS: dict[str, Any] = {"frequency": "weekly", "weekday": 6, "time": "03:30"}  # weekday: Monday is 0
SETTINGS = ("backup", "schedule")

Log = Callable[[str], None]


class HookRefused(RuntimeError):
    """Kopia should skip this backup; the message says why."""


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- the schedule


def validate(settings: dict[str, Any]) -> dict[str, Any]:
    """The settings as stored; raises ValueError for anything out of range."""
    frequency = str(settings.get("frequency", ""))
    if frequency not in FREQUENCIES:
        raise ValueError("Choose how often to back up.")
    weekday = settings.get("weekday", DEFAULTS["weekday"])
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
        raise ValueError("Choose a day of the week.")
    value = str(settings.get("time", ""))
    try:
        hours, minutes = value.split(":")
        if len(value) != 5 or not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
            raise ValueError
    except ValueError:
        raise ValueError("Choose a time as HH:MM, for example 03:30.") from None
    return {"frequency": frequency, "weekday": weekday, "time": value}


def settings(paths: RuntimePaths | None = None) -> dict[str, Any]:
    stored = records.get(*SETTINGS, paths or RuntimePaths())
    try:
        return {**validate({**DEFAULTS, **stored}), "changed_at": str(stored.get("changed_at") or "")}
    except ValueError:
        return {**DEFAULTS, "changed_at": ""}


def save(new: dict[str, Any], actor: str, paths: RuntimePaths | None = None) -> dict:
    """Store the household's choice; the worker writes it into Kopia's policy within a minute."""
    clean = validate(new)
    when = _now().isoformat(timespec="seconds")
    records.put(*SETTINGS, {**clean, "changed_at": when, "changed_by": actor}, paths or RuntimePaths())
    return settings(paths)


def crontab(chosen: dict[str, Any]) -> str:
    """The schedule as one crontab line for Kopia, or "" when scheduled backups are off."""
    if chosen["frequency"] == "off":
        return ""
    hour, minute = (int(part) for part in chosen["time"].split(":"))
    if chosen["frequency"] == "weekly":
        return f"{minute} {hour} * * {(chosen['weekday'] + 1) % 7}"  # cron counts from Sunday
    every = _EVERY[chosen["frequency"]]
    return f"{minute} {','.join(str(h) for h in range(hour % every, 24, every))} * * *"


def global_policy(chosen: dict[str, Any]) -> list[str]:
    cron = crontab(chosen)
    return [*backups.POLICY, f"--snapshot-time-crontab={cron or 'inherit'}", "--run-missed=true"]


# ---------------------------------------------------------------- sources and mounts


def sources(root: Path, paths: RuntimePaths | None = None) -> dict[str, list[tuple[str, Path]]]:
    """Every backup source and its folders: Mu3Lab itself, each core app and each installed app with data."""
    paths = paths or RuntimePaths()
    found: dict[str, list[tuple[str, Path]]] = {
        backups.PLATFORM_SOURCE: [("control", platform_backup.export_folder(paths)), ("projects", paths.projects)]
    }
    for service in load().services:
        if not service.manifest.backup.include:
            continue
        if service.stage == "optional" and not (paths.projects / service.id / "docker-compose.yml").is_file():
            continue
        folders = [
            folder
            for folder in backed_up(service, data_directories(service, root))
            if folder.is_dir() and not folder.is_symlink()
        ]
        if folders:
            found[service.id] = [(folder.name, folder) for folder in folders]
    return found


def mounts(found: dict[str, list[tuple[str, Path]]]) -> list[str]:
    return sorted(f"{path}:/data/{source}/{name}:ro" for source, folders in found.items() for name, path in folders)


def _inodes(found: dict[str, list[tuple[str, Path]]]) -> dict[str, int]:
    result = {}
    for folders in found.values():
        for _name, path in folders:
            try:
                result[str(path)] = os.stat(path).st_ino
            except OSError:
                result[str(path)] = 0
    return result


def service_id() -> str:
    from ctl.platform_apps import by_capability

    return by_capability(CAPABILITY).id


def project(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).projects / service_id()


def installed(paths: RuntimePaths | None = None) -> bool:
    return (project(paths) / "docker-compose.yml").is_file()


def write_sources(found: dict[str, list[tuple[str, Path]]], paths: RuntimePaths | None = None) -> Path:
    """The Compose override that mounts every source's folders into the server, read-only."""
    target = project(paths) / SOURCES_FILE
    document = {"services": {CONTAINER: {"volumes": mounts(found)}}}
    write_atomic(target, yaml.safe_dump(document, sort_keys=True).encode())
    target.chmod(0o640)
    return target


# ---------------------------------------------------------------- talking to Kopia


def _secrets(paths: RuntimePaths | None = None) -> dict[str, str]:
    return read_runtime_env(project(paths) / ".env")


def hook_token(paths: RuntimePaths | None = None) -> str:
    return _secrets(paths).get("MU3LAB_BACKUP_HOOK_TOKEN", "")


def token_valid(presented: str, paths: RuntimePaths | None = None) -> bool:
    expected = hook_token(paths)
    return bool(expected) and len(expected) >= 32 and hmac.compare_digest(presented.encode(), expected.encode())


def _control(method: str, path: str, params: dict[str, str] | None = None, paths: RuntimePaths | None = None) -> Any:
    """One call to the server's control API; raises OSError when the server cannot be asked."""
    password = _secrets(paths).get("KOPIA_SERVER_CONTROL_PASSWORD", "")
    if not password:
        raise OSError("the backup service has no control password yet")
    url = f"{ADDRESS}/api/v1/control/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    credentials = base64.b64encode(f"{CONTROL_USER}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {credentials}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, ValueError) as exc:
        raise OSError(f"the backup service did not answer: {exc}") from exc


def source_status(paths: RuntimePaths | None = None) -> dict[str, dict[str, Any]]:
    """Each source the server knows (by Mu3Lab name): its status, newest backup and next scheduled run."""
    listing = _control("GET", "sources", paths=paths)
    result = {}
    for item in listing.get("sources") or []:
        path = str((item.get("source") or {}).get("path") or "")
        if path.startswith("/data/"):
            result[path.removeprefix("/data/")] = item
    return result


def trigger(source_ids: list[str], paths: RuntimePaths | None = None) -> None:
    """Back these sources up now, with the same actions as a scheduled run. Raises OSError."""
    for source in source_ids:
        _control(
            "POST",
            "trigger-snapshot",
            {"userName": backups.HOST, "host": backups.HOST, "path": f"/data/{source}"},
            paths,
        )


# ---------------------------------------------------------------- the actions (called through the API)


def _result(source: str, ok: bool, detail: str, paths: RuntimePaths) -> None:
    records.put(RESULTS, source, {"ok": ok, "detail": detail, "at": _now().isoformat(timespec="seconds")}, paths)


def _wait(store: JobStore, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + HOOK_WAIT_SECONDS
    while time.monotonic() < deadline:
        job = store.get(job_id) or {}
        if job.get("state") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(1)
    return {"state": "failed", "detail": "The worker did not get to it in time."}


def _queue(store: JobStore, source: str, action: str, detail: str, since: str) -> dict[str, Any]:
    job = store.create(
        kind="lifecycle",
        service_id=source,
        action=action,
        actor=ACTOR,
        namespace="backup.hooks",
        detail=detail,
        idempotency_key=f"{action}:{source}:{since}",
    )
    return _wait(store, job["id"])


def before(source: str, store: JobStore, root: Path, paths: RuntimePaths | None = None) -> str:
    """Kopia is about to back up ``source``: make it consistent. Raises HookRefused to skip it."""
    paths = paths or RuntimePaths()
    if source == backups.PLATFORM_SOURCE:
        platform_backup.export_control_plane(paths)
        return "Mu3Lab's own state is ready to be saved."
    try:
        service = load().get(source)
    except RegistryError:
        raise HookRefused("Mu3Lab does not back up that folder.") from None
    try:
        found = sources(root, paths).get(source)
        if not found:
            raise HookRefused(f"{service.name} has no data to back up here.")
        recorded = records.get(*STATE, paths).get("inodes") or {}
        if any(recorded.get(str(path)) != os.stat(path).st_ino for _name, path in found):
            raise HookRefused(f"{service.name}'s folders changed since the backup service started; it restarts soon.")
        if records.get(HOLDS, source, paths):
            raise HookRefused(f"{service.name} is already being backed up.")
        if in_progress.get(source, paths):
            raise HookRefused(f"{service.name} needs attention after an interrupted restore or update.")
        release = app_releases.installed(service, root, paths)
        backups.note_release(
            source,
            app_releases.remember(source, release, paths) if release else "",
            release.version if release else "",
            [name for name, _path in found],
            paths,
        )
        running = app_control.was_running(ControlState.runtime(paths), service)
        since = _now().isoformat(timespec="seconds")
        records.put(HOLDS, source, {"since": since, "was_running": running}, paths)
        if running:
            try:
                job = _queue(store, source, "backup_stop", f"Stopping {service.name} for its backup.", since)
            except JobConflict:
                records.delete(HOLDS, source, paths)
                raise HookRefused(
                    f"Another task is running on {service.name}; its backup waits for the next run."
                ) from None
            if job.get("state") != "succeeded":
                try:
                    _queue(store, source, "backup_start", f"Starting {service.name} again.", since)
                    records.delete(HOLDS, source, paths)
                except JobConflict:
                    pass  # the stop is still waiting for the worker; the stuck-app check starts it after
                raise HookRefused(f"{service.name} could not be stopped for its backup: {job.get('detail', '')}")
        return f"{service.name} is ready to be saved."
    except HookRefused as refused:
        _result(source, False, str(refused), paths)
        raise


def after(source: str, store: JobStore, root: Path, paths: RuntimePaths | None = None) -> str:
    """Kopia finished backing up ``source``: let the app run again."""
    paths = paths or RuntimePaths()
    if source == backups.PLATFORM_SOURCE:
        platform_backup.clear_export(paths)
        _result(source, True, "", paths)
        return "Mu3Lab's own state was saved."
    hold = records.get(HOLDS, source, paths)
    if not hold:
        return "Nothing to start again."
    detail = ""
    if hold.get("was_running"):
        try:
            job = _queue(store, source, "backup_start", "Starting the app again after its backup.", str(hold["since"]))
        except JobConflict:
            # Another job holds the app; the stuck-app check starts it once that job is done.
            _result(source, False, "It was not started again straight away; Mu3Lab retries shortly.", paths)
            return "The app starts again shortly."
        if job.get("state") != "succeeded":
            detail = f"It did not start again: {job.get('detail', '')}"
    records.delete(HOLDS, source, paths)
    _result(source, not detail, detail, paths)
    return detail or "Started again."


def holding(source: str, paths: RuntimePaths | None = None) -> bool:
    """The app is stopped for its backup right now; nothing else may change it."""
    return bool(records.get(HOLDS, source, paths or RuntimePaths()))


# ---------------------------------------------------------------- keeping the server in step (worker)


def _container(log: Log) -> dict[str, Any]:
    rc, output = actions.docker_cmd(
        [
            "docker",
            "ps",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project=mu3lab-{service_id()}",
            "--filter",
            f"label=com.docker.compose.service={CONTAINER}",
        ],
        lambda _line: None,
        timeout=30,
    )
    ids = output.split() if rc == 0 else []
    if not ids:
        return {}
    rc, output = actions.docker_cmd(["docker", "inspect", ids[0]], lambda _line: None, timeout=30)
    try:
        details = json.loads(output)[0] if rc == 0 else {}
    except (ValueError, IndexError):
        return {}
    return {
        "id": ids[0],
        "started_at": str((details.get("State") or {}).get("StartedAt") or ""),
        "mounts": sorted(
            f"{item['Source']}:{item['Destination']}:ro"
            for item in details.get("Mounts") or []
            if str(item.get("Destination", "")).startswith("/data/")
        ),
    }


def _exec(container: str, args: list[str], log: Log) -> None:
    rc, output = actions.docker_cmd(["docker", "exec", container, "kopia", *args], log, timeout=300)
    if rc:
        raise OSError(f"kopia {' '.join(args[:2])} failed: {output[-300:]}")


def _recreate(root: Path, log: Log, paths: RuntimePaths) -> None:
    from ctl.lifecycle import compose_settings

    service = load().get(service_id())
    env, extra = compose_settings.settings(service, project(paths), paths)
    rc, output = actions.compose_up(project(paths), log, env=env, extra_files=extra, recreate=True, wait_timeout=120)
    if rc:
        raise OSError(f"the backup service did not start: {output[-300:]}")


def _restart_stuck(store: JobStore, status: dict[str, dict] | None, log: Log, paths: RuntimePaths) -> None:
    """Start any app a backup stopped whose after-action never came (the backup failed or Kopia restarted)."""
    for source in records.names(HOLDS, paths):
        hold = records.get(HOLDS, source, paths)
        try:
            since = datetime.fromisoformat(str(hold.get("since")))
        except (TypeError, ValueError):
            since = _now() - UNREACHABLE
        uploading = status is not None and (status.get(source) or {}).get("status") == "UPLOADING"
        limit = STUCK if status is not None else UNREACHABLE
        if uploading or _now() - since < limit:
            continue
        log(f"A backup of {source} ended without starting it again; starting it now.")
        if hold.get("was_running"):
            try:
                store.create(
                    kind="lifecycle",
                    service_id=source,
                    action="backup_start",
                    actor=ACTOR,
                    namespace="backup.hooks",
                    detail="Starting the app again: its backup ended without doing so.",
                    idempotency_key=f"backup_start:{source}:{hold.get('since')}",
                )
            except JobConflict:
                continue  # its after-action is starting it right now
        records.delete(HOLDS, source, paths)
        _result(source, False, "The backup did not finish; Kopia's log has the details.", paths)


def _note_reachable(answered: bool, paths: RuntimePaths) -> None:
    down = records.get(*REACHABLE, paths)
    if answered and down:
        records.delete(*REACHABLE, paths)
    elif not answered and not down.get("since"):
        records.put(*REACHABLE, {"since": _now().isoformat(timespec="seconds")}, paths)


def reconcile(store: JobStore, root: Path, log: Log, paths: RuntimePaths | None = None) -> None:
    """Keep the server's folders, actions, schedule and Mu3Lab's records in step. Called about once a minute."""
    paths = paths or RuntimePaths()
    if not installed(paths):
        return
    found = sources(root, paths)
    wanted = mounts(found)
    state = records.get(*STATE, paths)
    try:
        status: dict[str, dict] | None = source_status(paths)
    except OSError:
        status = None
    _note_reachable(status is not None, paths)
    _restart_stuck(store, status, log, paths)
    container = _container(log)
    if not container:
        return  # stopped by an administrator, or not started yet: nothing to change
    busy = bool(records.names(HOLDS, paths)) or any(
        item.get("status") not in {"IDLE", "PAUSED"} for item in (status or {}).values()
    )
    inodes = _inodes(found)
    restarted_elsewhere = container["started_at"] != state.get("started_at")
    folders_moved = not restarted_elsewhere and state.get("inodes") != inodes
    if container["mounts"] != wanted or folders_moved:
        if busy:
            return  # never cut a running backup short; try again in a minute
        log("Restarting the backup service so it sees every app's current folders.")
        write_sources(found, paths)
        _recreate(root, log, paths)
        container = _container(log)
        state = {}
    elif restarted_elsewhere:
        state = {}  # started by someone else with these same folders: it sees them as they are now
    chosen = settings(paths)
    policy = global_policy(chosen)
    applied = dict(state.get("policies") or {})
    try:
        if state.get("global") != policy:
            _exec(container["id"], ["policy", "set", "--global", *policy], log)
        for source in found:
            if applied.get(source) != HOOK:
                _exec(
                    container["id"],
                    [
                        "policy",
                        "set",
                        f"/data/{source}",
                        f"--before-snapshot-root-action={HOOK}",
                        f"--after-snapshot-root-action={HOOK}",
                        "--action-command-mode=essential",
                        f"--action-command-timeout={ACTION_TIMEOUT}",
                    ],
                    log,
                )
                applied[source] = HOOK
        _control("POST", "refresh", paths=paths)
    except OSError as exc:
        log(f"The backup service's settings are not up to date yet: {exc}")
        policy = state.get("global") or []
    records.put(
        *STATE,
        {
            "started_at": container.get("started_at", ""),
            "inodes": inodes,
            "global": policy,
            "policies": applied,
            "checked_at": _now().isoformat(timespec="seconds"),
        },
        paths,
    )
    for source, item in (status or {}).items():
        finished = str((item.get("lastSnapshot") or {}).get("endTime") or "")
        if finished and source in found:
            backups.record_backup(source, finished, paths)


# ---------------------------------------------------------------- what the dashboard shows


def problems(paths: RuntimePaths | None = None) -> list[str]:
    """Sources whose last backup attempt failed, newest first."""
    paths = paths or RuntimePaths()
    failed = []
    for source in records.names(RESULTS, paths):
        result = records.get(RESULTS, source, paths)
        if not result.get("ok"):
            failed.append((str(result.get("at") or ""), f"{source}: {result.get('detail') or 'the backup failed'}"))
    return [text for _at, text in sorted(failed, reverse=True)]


def status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """The schedule, Kopia's next run, and what needs fixing."""
    paths = paths or RuntimePaths()
    chosen = settings(paths)
    try:
        if not installed(paths):
            raise OSError("Kopia backups is not installed")
        known = source_status(paths)
        upcoming = sorted(str(item.get("nextSnapshotTime") or "") for item in known.values())
        next_run = next((stamp for stamp in upcoming if stamp), "") if chosen["frequency"] != "off" else ""
        running = True
    except OSError:
        next_run, running = "", False
    return {
        "frequency": chosen["frequency"],
        "weekday": chosen["weekday"],
        "time": chosen["time"],
        "next_run_at": next_run,
        "service_running": running,
        "service_installed": installed(paths),
        "problems": problems(paths)[:5],
    }


# How long past its due time a backup may be before Home asks for attention.
GRACE = timedelta(hours=12)
INTERVALS = {
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}
# With scheduled backups off, Home asks once the newest backup is this old.
UNSCHEDULED_LIMIT = timedelta(days=7)
# How long the server may not answer (a restart, a restore swapping folders) before Home says so.
DOWN_LIMIT = timedelta(minutes=10)


def _days(age: timedelta) -> str:
    days = int(age.total_seconds() // 86400)
    if days < 1:
        return f"{max(1, int(age.total_seconds() // 3600))} hours ago"
    return "yesterday" if days == 1 else f"{days} days ago"


def attention(paths: RuntimePaths | None = None, *, now: datetime | None = None) -> list[dict]:
    """What Home tells an administrator about backups: nothing while they keep up with the schedule.

    Mu3Lab's own backup (its database and keys) is the measure: an app backup
    cannot be restored onto a new computer without it.
    """
    paths = paths or RuntimePaths()
    now = now or _now()
    if not installed(paths):
        # Backups are the owner's choice: one quiet reminder, which Home lets them dismiss.
        return [
            {
                "key": "backup-service-missing",
                "message": "Backups are off. Install Kopia backups from Apps to back up on a schedule.",
            }
        ]
    chosen = settings(paths)
    scheduled = chosen["frequency"] != "off"
    limit = INTERVALS[chosen["frequency"]] + GRACE if scheduled else UNSCHEDULED_LIMIT
    newest = str(backups.protection(backups.PLATFORM_SOURCE, paths).get("completed_at") or "")
    off = "" if scheduled else " Scheduled backups are off."
    items: list[dict] = []
    down = str(records.get(*REACHABLE, paths).get("since") or "")
    if down and installed(paths) and now - datetime.fromisoformat(down) > DOWN_LIMIT:
        name = load().get(service_id()).name
        items.append(
            {
                "key": "backup-service-stopped",
                "message": f"The backup service ({name}) is not running, so no backups are being made. "
                "Start it from its page in Apps.",
            }
        )
    if newest:
        try:
            age = now - datetime.fromisoformat(newest.replace("Z", "+00:00"))
        except ValueError:
            age = None
        if age is not None and age > limit:
            items.append({"key": "backup-overdue", "message": f"Mu3Lab's last backup was {_days(age)}.{off}"})
    else:
        # A new install is asked for its first backup straight away.
        items.append({"key": "backup-missing", "message": f"Mu3Lab has not been backed up yet.{off}"})
    failed = problems(paths)
    if failed:
        noun = "backup" if len(failed) == 1 else "backups"
        items.append({"key": "backup-failed", "message": f"{len(failed)} {noun} did not finish last time."})
    return items
