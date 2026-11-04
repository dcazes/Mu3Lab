"""Consistent copies of an app's databases, made while the app keeps running.

A backup copies files as they are, so a database written to during the copy could
be saved half-changed. Instead of stopping the app, Mu3Lab writes a consistent
copy of each of its databases into the app's data folder just before every backup,
under ``.mu3lab-backup/``, the way database engines expect to be backed up:

- **Postgres and MariaDB**, found as a service that keeps ``/var/lib/postgresql``
  or ``/var/lib/mysql`` in a data folder: the engine's own dump, run inside that
  container. Kopia leaves the live database folder out (``kopia_rules``).
- **SQLite**, found as any file that starts with SQLite's header: SQLite's online
  backup, from a networkless helper container. The live file is saved too; a
  restore puts the copy in its place.
- **Caches** the app builds again (Redis, Valkey and RabbitMQ folders, and what the
  manifest's ``backup.ignore`` lists): left out.

A restore unpacks a backup beside the live data, as before. ``prepare`` then puts
each SQLite copy in place and empties each dumped database's folder; after the
swap, ``load`` starts only the database containers, loads each dump, and only
then does the app itself start. A backup from before this, with the live
database folder inside and no dumps, restores as it always did.

The database containers of a stopped app are started on their own for a dump or
a load, and stopped again afterwards.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from ctl import actions
from ctl.jobs import redact
from ctl.lifecycle import app_control, compose_settings
from ctl.lifecycle.uninstall import backed_up, data_directories
from ctl.registry import Service
from ctl.runtime import RuntimePaths

FOLDER = ".mu3lab-backup"
# The pinned Python the chat connectors are built on; it has SQLite's online backup.
HELPER_IMAGE = "python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322"
ENGINES = {"/var/lib/postgresql": "postgres", "/var/lib/mysql": "mariadb"}
CACHE_IMAGE = re.compile(r"(^|/)(redis|valkey|rabbitmq)(:|@|$)")
READY_SECONDS = 180
DUMP_SECONDS = 6 * 3600

_MOUNT = re.compile(r"^\$\{MU3LAB_DATA_ROOT(?::-[^}]*)?\}/([a-z0-9-]+(?:/[^:/]+)*):(/[^:]*)")
_ROOT_PASSWORD = '"${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}"'
# Shell run inside the database's own container: ready on TCP (the server an image
# starts while it sets itself up listens on its socket only), dump, and load.
READY = {
    "postgres": 'pg_isready -q -h 127.0.0.1 -U "${POSTGRES_USER:-postgres}"',
    "mariadb": f"MYSQL_PWD={_ROOT_PASSWORD} mariadb-admin -uroot -h127.0.0.1 --protocol=tcp ping",
}
DUMP = {
    "postgres": 'exec pg_dumpall --username="${POSTGRES_USER:-postgres}"',
    "mariadb": (
        'set -- --all-databases; [ -n "$MARIADB_DATABASE" ] && set -- --databases "$MARIADB_DATABASE"; '
        f"MYSQL_PWD={_ROOT_PASSWORD} exec mariadb-dump -uroot --single-transaction --routines --triggers "
        '--events "$@"'
    ),
}
# Each engine ends a finished dump with this line; a dump without it was cut short.
COMPLETE = {"postgres": "PostgreSQL database cluster dump complete", "mariadb": "-- Dump completed"}
LOAD = {
    "postgres": 'exec psql -X -q -v ON_ERROR_STOP=0 --username="${POSTGRES_USER:-postgres}" --dbname=postgres',
    "mariadb": f"MYSQL_PWD={_ROOT_PASSWORD} exec mariadb -uroot",
}

# The helper's scripts. Each runs as root over one data folder mounted at /folder
# (its first argument), because the folders belong to the apps' container users.
_WRITE = """
import os, sys
target, marker = sys.argv[1], sys.argv[2].encode()
os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
partial, tail = target + ".partial", b""
with open(partial, "wb") as out:
    os.fchmod(out.fileno(), 0o600)
    while chunk := sys.stdin.buffer.read(1 << 20):
        out.write(chunk)
        tail = (tail + chunk)[-4096:]
    out.flush()
    os.fsync(out.fileno())
if marker not in tail:
    os.unlink(partial)
    sys.exit("the dump ended before it was complete")
os.replace(partial, target)
"""
# Postgres dumps empty the search path; Immich's vector extensions need it to load
# (Immich's own restore guide makes the same change). Other dumps name every object in full.
_READ = """
import sys
old = b"SELECT pg_catalog.set_config('search_path', '', false);"
new = b"SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);"
postgres = sys.argv[2] == "postgres"
with open(sys.argv[1], "rb") as dump:
    for line in dump:
        sys.stdout.buffer.write(new + line[len(old):] if postgres and line.startswith(old) else line)
"""
_SQLITE = """
import json, os, shutil, sqlite3, stat, sys
root, skip = sys.argv[1], set(json.loads(sys.argv[2]))
copies, fresh = os.path.join(root, ".mu3lab-backup", "sqlite"), os.path.join(root, ".mu3lab-backup", "sqlite.new")
shutil.rmtree(fresh, ignore_errors=True)
copied, failed = [], []
for top, folders, files in os.walk(root):
    here = os.path.relpath(top, root)
    folders[:] = [name for name in folders if os.path.normpath(os.path.join(here, name)) not in skip]
    for name in files:
        path, relative = os.path.join(top, name), os.path.normpath(os.path.join(here, name))
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size < 512:
                continue
            with open(path, "rb") as handle:
                if handle.read(16) != b"SQLite format 3\\x00":
                    continue
        except OSError:
            continue
        try:
            # Read as the file's owner, so any journal SQLite opens beside it stays the app's.
            switch = os.geteuid() == 0
            if switch:
                os.setegid(info.st_gid)
                os.seteuid(info.st_uid)
            try:
                source, memory = sqlite3.connect(path, timeout=20), sqlite3.connect(":memory:")
                source.backup(memory)
                source.close()
            finally:
                if switch:
                    os.seteuid(0)
                    os.setegid(0)
            target = os.path.join(fresh, relative)
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            with open(target, "wb") as out:
                out.write(memory.serialize())
            memory.close()
            copied.append(relative)
        except (OSError, sqlite3.Error) as exc:
            failed.append(f"{relative}: {exc}")
shutil.rmtree(copies, ignore_errors=True)
if os.path.isdir(fresh):
    os.rename(fresh, copies)
print(json.dumps({"copied": copied, "failed": failed}))
"""
_PREPARE = """
import json, os, shutil, sys
root, databases = sys.argv[1], json.loads(sys.argv[2])
saved = os.path.join(root, ".mu3lab-backup")
load, placed = [], []
for inside, service in databases.items():
    if os.path.isfile(os.path.join(saved, service + ".sql")):
        # The dump wins over any copy of the live folder: the database starts empty and is loaded.
        target = os.path.join(root, inside)
        shutil.rmtree(target, ignore_errors=True)
        os.makedirs(target, mode=0o700)
        load.append(service)
copies = os.path.join(saved, "sqlite")
for top, _folders, files in os.walk(copies):
    for name in files:
        relative = os.path.relpath(os.path.join(top, name), copies)
        target = os.path.join(root, relative)
        try:
            info = os.stat(target)
        except FileNotFoundError:
            continue
        partial = target + ".mu3lab-partial"
        shutil.copyfile(os.path.join(top, name), partial)
        os.chown(partial, info.st_uid, info.st_gid)
        os.chmod(partial, info.st_mode & 0o7777)
        os.replace(partial, target)
        for journal in ("-wal", "-shm", "-journal"):
            try:
                os.unlink(target + journal)
            except FileNotFoundError:
                pass
        placed.append(relative)
print(json.dumps({"load": load, "placed": placed}))
"""

Log = Callable[[str], None]


class DumpFailed(RuntimeError):
    """A database could not be copied or loaded; the message is safe to show."""


@dataclass(frozen=True)
class Database:
    service: str  # the Compose service
    engine: str
    path: str  # its folder under the data root, like "immich/postgres"

    @property
    def folder(self) -> str:
        return self.path.split("/", 1)[0]

    @property
    def inside(self) -> str:
        return self.path.split("/", 1)[-1]

    @property
    def dump(self) -> str:
        return f"{FOLDER}/{self.service}.sql"


def _definitions(service: Service, root: Path, paths: RuntimePaths) -> dict[str, list[dict]]:
    """Each Compose service's definitions, from the installed deployment when there is one (as for its folders)."""
    installed = paths.projects / service.id
    files = sorted(installed.glob("docker-compose*.yml")) if installed.is_dir() else []
    found: dict[str, list[dict]] = {}
    for compose_file in files or sorted(service.compose_path(root).glob("docker-compose*.yml")):
        try:
            document = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        for name, definition in (document.get("services") or {}).items():
            found.setdefault(str(name), []).append(definition or {})
    return found


def _mounts(definitions: list[dict]) -> list[tuple[str, str]]:
    """(folder under the data root, path in the container) for each data mount."""
    found = []
    for definition in definitions:
        for volume in definition.get("volumes") or []:
            text = f"{volume.get('source')}:{volume.get('target')}" if isinstance(volume, dict) else str(volume)
            match = _MOUNT.match(text)
            if match:
                found.append((match.group(1), match.group(2).rstrip("/")))
    return found


def _folders(service: Service, root: Path, paths: RuntimePaths) -> list[str]:
    """The app's data folders a backup holds."""
    if not service.manifest.backup.include:
        return []
    return [folder.name for folder in backed_up(service, data_directories(service, root, paths=paths))]


def databases(service: Service, root: Path, paths: RuntimePaths | None = None) -> list[Database]:
    """The app's Postgres and MariaDB databases in the folders its backup holds."""
    paths = paths or RuntimePaths()
    folders = set(_folders(service, root, paths))
    found = []
    for name, definitions in sorted(_definitions(service, root, paths).items()):
        for path, target in _mounts(definitions):
            engine = next((engine for prefix, engine in ENGINES.items() if target.startswith(prefix)), None)
            if engine and path.split("/", 1)[0] in folders:
                found.append(Database(name, engine, path))
    return found


def ignored(service: Service, root: Path, paths: RuntimePaths | None = None) -> list[str]:
    """Folders under the data root a backup leaves out: live databases (their dumps are saved) and caches."""
    paths = paths or RuntimePaths()
    folders = set(_folders(service, root, paths))
    found = {database.path for database in databases(service, root, paths)}
    for definitions in _definitions(service, root, paths).values():
        if any(CACHE_IMAGE.search(str(definition.get("image") or "")) for definition in definitions):
            found.update(path for path, _target in _mounts(definitions))
    found.update(service.manifest.backup.ignore)
    return sorted(path for path in found if path.split("/", 1)[0] in folders)


def kopia_rules(service: Service, root: Path, paths: RuntimePaths | None = None) -> list[str]:
    """Kopia ignore rules for the source ``/data/<app>``, where each folder is ``/data/<app>/<folder>``."""
    return [f"/{path}/" for path in ignored(service, root, paths)]


def _helper(folder: Path, script: str, *args: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--user",
        "0:0",
        "-v",
        f"{folder}:/folder",
        HELPER_IMAGE,
        "python",
        "-c",
        script,
        *args,
    ]


def _quiet(_line: str) -> None:
    pass


def _result(output: str) -> dict:
    """The JSON line a helper script printed last."""
    for line in reversed(output.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


class _Containers:
    """The database containers of one app: started alone when stopped, and stopped again after."""

    def __init__(self, service: Service, root: Path, chosen: list[Database], log: Log, *, keep: bool) -> None:
        self.service, self.chosen, self.log, self.keep = service, chosen, log, keep
        self.project = app_control.project(service, root)
        try:
            self.env, self.extra = compose_settings.settings(service, self.project)
        except RuntimeError as exc:
            raise DumpFailed(f"{service.name}'s databases could not be reached: {exc}") from exc
        self.started: list[str] = []

    def compose(self, *args: str) -> list[str]:
        return actions.compose_argv(self.project, self.extra, *args)

    def __enter__(self) -> _Containers:
        rc, output = actions.docker_cmd(
            self.compose("ps", "--status", "running", "--services"), _quiet, timeout=60, env=self.env
        )
        running = set(output.split()) if rc == 0 else set()
        wanted = sorted({database.service for database in self.chosen} - running)
        if wanted:
            self.log(f"Starting {self.service.name}'s database on its own.")
            rc, output = actions.compose_up(
                self.project, self.log, timeout=600, env=self.env, extra_files=self.extra, services=wanted
            )
            if rc:
                raise DumpFailed(f"{self.service.name}'s database did not start: {redact(output)[-300:]}")
            self.started = wanted
        for database in self.chosen:
            self._ready(database)
        return self

    def _ready(self, database: Database) -> None:
        deadline = time.monotonic() + READY_SECONDS
        while True:
            argv = self.compose("exec", "-T", database.service, "sh", "-c", READY[database.engine])
            rc, _output = actions.docker_cmd(argv, _quiet, timeout=30, env=self.env)
            if rc == 0:
                return
            if time.monotonic() > deadline:
                raise DumpFailed(f"{self.service.name}'s database ({database.service}) did not become ready.")
            time.sleep(2)

    def __exit__(self, *_exc: object) -> None:
        if self.started and not self.keep:
            actions.docker_cmd(self.compose("stop", *self.started), self.log, timeout=300, env=self.env)


def _sqlite(service: Service, root: Path, log: Log, paths: RuntimePaths) -> tuple[int, list[str]]:
    """Copy every SQLite database in the app's folders with SQLite's online backup; (copies, failures)."""
    left_out = ignored(service, root, paths)
    copied, failed = 0, []
    for name in _folders(service, root, paths):
        if name in left_out or not (paths.data / name).is_dir():
            continue
        skip = [FOLDER, *(path.split("/", 1)[1] for path in left_out if path.startswith(f"{name}/"))]
        rc, output = actions.docker_cmd(
            _helper(paths.data / name, _SQLITE, "/folder", json.dumps(skip)), log, timeout=DUMP_SECONDS
        )
        result = _result(output)
        if rc or not result:
            failed.append(f"{name}: {redact(output)[-200:]}")
            continue
        copied += len(result.get("copied") or [])
        failed.extend(f"{name}/{item}" for item in result.get("failed") or [])
    return copied, failed


def write(service: Service, root: Path, log: Log, paths: RuntimePaths | None = None) -> str:
    """Write a consistent copy of each of the app's databases into its folders. Raises DumpFailed."""
    paths = paths or RuntimePaths()
    chosen = databases(service, root, paths)
    for database in chosen:
        if "/" not in database.path:
            raise DumpFailed(f"{service.name} keeps its database in a folder of its own, which Mu3Lab cannot dump.")
    if chosen:
        with _Containers(service, root, chosen, log, keep=False) as containers:
            for database in chosen:
                producer = containers.compose("exec", "-T", database.service, "sh", "-c", DUMP[database.engine])
                consumer = _helper(
                    paths.data / database.folder, _WRITE, f"/folder/{database.dump}", COMPLETE[database.engine]
                )
                rc, output = actions.docker_pipe(producer, consumer, log, timeout=DUMP_SECONDS, env=containers.env)
                if rc:
                    raise DumpFailed(
                        f"{service.name}'s database ({database.service}) could not be copied: {redact(output)[-300:]}"
                    )
    copied, failed = _sqlite(service, root, log, paths)
    for failure in failed:
        # The live file is saved as well, so the backup still holds it; only its copy is missing.
        log(f"A SQLite database could not be copied, so its file is saved as it was: {failure}")
    parts = [f"{len(chosen)} database dump{'s' if len(chosen) != 1 else ''}"] if chosen else []
    if copied:
        parts.append(f"{copied} SQLite cop{'ies' if copied != 1 else 'y'}")
    return f"Copied {service.name}'s databases ({', '.join(parts)})." if parts else f"{service.name} has no databases."


def prepare(
    service: Service, root: Path, staged: dict[str, Path], log: Log, paths: RuntimePaths | None = None
) -> list[str]:
    """Ready unpacked folders (``{folder: staging}``) for the swap; returns the database services to load.

    Each SQLite copy takes its live file's place, and each database with a dump
    gets an empty folder. A backup with no copies is left as it was.
    """
    paths = paths or RuntimePaths()
    chosen = databases(service, root, paths)
    load: list[str] = []
    for name, staging in staged.items():
        inside = {database.inside: database.service for database in chosen if database.folder == name}
        rc, output = actions.docker_cmd(_helper(staging, _PREPARE, "/folder", json.dumps(inside)), log, timeout=3600)
        result = _result(output)
        if rc or not result:
            raise DumpFailed(f"The backup of {name} could not be readied: {redact(output)[-300:]}")
        load.extend(result.get("load") or [])
        if result.get("placed"):
            log(f"Put {len(result['placed'])} SQLite copies of {name} in place.")
    return sorted(set(load))


def load(
    service: Service,
    root: Path,
    services: list[str],
    log: Log,
    paths: RuntimePaths | None = None,
    *,
    keep_running: bool = False,
) -> None:
    """Load the saved dumps into the (empty) databases ``services``, before the rest of the app starts."""
    paths = paths or RuntimePaths()
    chosen = [database for database in databases(service, root, paths) if database.service in services]
    if not chosen:
        return
    with _Containers(service, root, chosen, log, keep=keep_running) as containers:
        for database in chosen:
            producer = _helper(paths.data / database.folder, _READ, f"/folder/{database.dump}", database.engine)
            consumer = containers.compose("exec", "-T", database.service, "sh", "-c", LOAD[database.engine])
            rc, output = actions.docker_pipe(producer, consumer, log, timeout=DUMP_SECONDS, env=containers.env)
            # A fresh database already has the app's role and database; those lines may fail.
            errors = [line for line in output.splitlines() if "ERROR" in line and "already exists" not in line]
            if rc or errors:
                raise DumpFailed(
                    f"{service.name}'s database ({database.service}) could not be loaded: "
                    f"{redact(chr(10).join(errors) or output)[-300:]}"
                )
            log(f"Loaded {service.name}'s database ({database.service}) from its backup.")
