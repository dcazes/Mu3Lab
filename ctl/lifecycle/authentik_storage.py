"""Move Authentik's data from Docker volumes into Mu3Lab's data folder.

Earlier installs kept Authentik's database, media and templates in Docker
named volumes, outside /srv/mu3lab/data, so copying that folder missed every
account. The Compose file now binds them under <data>/authentik. This moves an
existing install once: stop Authentik, copy each volume keeping owners and
permissions, compare checksums, record the move, start Authentik on the new
location. The old volumes are kept until the owner removes them.

Until the move is recorded, the old volumes are authoritative: nothing may
start Authentik on the new, empty location.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.runtime import RuntimePaths

VOLUMES = {
    "authentik_authentik-postgresql": "postgresql",
    "authentik_authentik-media": "media",
    "authentik_authentik-templates": "templates",
}
MARKER = ".mu3lab-storage-v2"
# Pinned tool image used only to copy and checksum files as root.
HELPER = "alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8"
LEGACY_OVERRIDE = "docker-compose.legacy-volumes.yml"

Log = Callable[[str], None]


def data_dir(paths: RuntimePaths = RuntimePaths()) -> Path:
    return paths.data / "authentik"


def _volume_exists(name: str) -> bool:
    rc, _ = actions.docker_cmd(["docker", "volume", "inspect", name], lambda _line: None, timeout=15)
    return rc == 0


def status(paths: RuntimePaths = RuntimePaths()) -> str:
    """ "ready" once moved (or on a fresh install), "needs_migration" while old volumes hold the data."""
    if (data_dir(paths) / MARKER).is_file():
        return "ready"
    return "needs_migration" if _volume_exists("authentik_authentik-postgresql") else "ready"


def mark_fresh(paths: RuntimePaths = RuntimePaths()) -> None:
    """A fresh install starts on the new location directly."""
    target = data_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    (target / MARKER).write_text("Authentik data lives here.\n", encoding="utf-8")


def _helper(argv: list[str], mounts: list[str], log: Log, timeout: int = 600) -> tuple[int, str]:
    command = ["docker", "run", "--rm", "--network", "none"]
    for mount in mounts:
        command += ["-v", mount]
    return actions.docker_cmd([*command, HELPER, "sh", "-c", *argv], log, timeout=timeout)


_CHECKSUMS = "cd {path} && find . -type f -exec md5sum {{}} + | sort -k 2 | md5sum && find . | wc -l"


def migrate(project: Path, env: dict[str, str], log: Log, paths: RuntimePaths = RuntimePaths()) -> tuple[bool, str]:
    target = data_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    log("Stopping Authentik to move its data.")
    rc, output = actions.compose_action(project, "stop", log, env=env)
    if rc:
        return False, "Authentik could not be stopped: " + output[-300:]
    for volume, name in VOLUMES.items():
        if not _volume_exists(volume):
            continue
        log(f"Copying {name}.")
        # Anything already at the new location predates the recorded move; set it aside.
        rc, output = _helper(
            [
                f'if [ -n "$(ls -A /to/{name} 2>/dev/null)" ]; then mv /to/{name} /to/{name}.set-aside-$(date +%s); fi; '
                f"mkdir -p /to/{name} && cp -a /from/. /to/{name}/ "
                f'&& chown "$(stat -c %u:%g /from)" /to/{name} && chmod "$(stat -c %a /from)" /to/{name}'
            ],
            [f"{volume}:/from:ro", f"{target}:/to"],
            log,
        )
        if rc:
            return _roll_back(project, env, log, f"copying {name} failed: {output[-300:]}")
        rc, output = _helper(
            [f"{_CHECKSUMS.format(path='/from')} && echo --- && {_CHECKSUMS.format(path=f'/to/{name}')}"],
            [f"{volume}:/from:ro", f"{target}:/to:ro"],
            log,
        )
        before, _, after = output.partition("---")
        if rc or not before.strip() or before.split() != after.split():
            return _roll_back(project, env, log, f"the copy of {name} did not match the original")
    (target / MARKER).write_text(
        "Authentik data lives here. The old Docker volumes (" + ", ".join(VOLUMES) + ") are kept as a "
        "fallback; remove them with `docker volume rm` once you are happy.\n",
        encoding="utf-8",
    )
    log("Starting Authentik on its new data folder.")
    rc, output = actions.compose_up(project, log, env=env, timeout=900, wait_timeout=600)
    if rc:
        (target / MARKER).unlink(missing_ok=True)
        return _roll_back(project, env, log, "Authentik did not start on the moved data: " + output[-300:])
    return True, f"Authentik's data now lives in {target}; the old volumes are kept as a fallback."


def _roll_back(project: Path, env: dict[str, str], log: Log, reason: str) -> tuple[bool, str]:
    """Start Authentik again on its original volumes so sign-in keeps working."""
    log(f"Moving Authentik's data failed ({reason}); starting it on its original volumes.")
    rc, output = actions.compose_up(
        project, log, env=env, extra_files=[project / LEGACY_OVERRIDE], timeout=900, wait_timeout=600
    )
    detail = "" if rc == 0 else f" Authentik also failed to restart on its original volumes: {output[-300:]}"
    return False, f"Authentik's data was not moved: {reason}.{detail}"
