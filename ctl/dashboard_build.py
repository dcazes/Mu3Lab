"""Build and debug the dashboard in the same pinned Node container."""

from __future__ import annotations

import os
from pathlib import Path

from ctl import actions
from ctl.bootstrap import stamps

NODE_IMAGE = "node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1"
COMMANDS = {
    "build": "npm ci && npm run build",
    "check": "npm ci && npm run check:api && npm run lint && npm run format:check && npm run typecheck && npm test",
    "dev": "npm ci && npm run dev -- --host 127.0.0.1",
    "api-schema": "npm ci && npm run gen:api",
    "format": "npm ci && npm run format",
}
# npm prints these when a download drops mid-way; a fresh attempt usually works.
NETWORK_ERRORS = ("ECONNRESET", "ETIMEDOUT", "EAI_AGAIN", "ENOTFOUND", "ECONNREFUSED", "npm error network")
BUILD_ATTEMPTS = 3


def command(root: Path, task: str) -> list[str]:
    argv = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-e",
        "npm_config_cache=/tmp/npm-cache",
        "-v",
        f"{root / 'dashboard'}:/src",
        "-w",
        "/src",
    ]
    if task == "dev":
        argv.extend(["--network", "host"])
    argv.extend([NODE_IMAGE, "sh", "-c", COMMANDS[task]])
    return argv


def build(root: Path, log) -> tuple[bool, str]:
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        rc, output = actions.docker_cmd_stream(command(root, "build"), log, timeout=1800)
        if not rc or not any(marker in output for marker in NETWORK_ERRORS):
            break
        if attempt < BUILD_ATTEMPTS:
            log(f"Download interrupted; trying again ({attempt + 1} of {BUILD_ATTEMPTS}).")
    if rc and any(marker in output for marker in NETWORK_ERRORS):
        return False, (
            "Could not download the dashboard's parts: the internet connection kept dropping. "
            "Check your connection, then run ./install.sh again."
        )
    if rc:
        tail = "\n".join(output.strip().splitlines()[-8:])
        return False, "Dashboard build failed. Review the build log and retry.\n" + tail
    if not (root / "dashboard/dist/index.html").is_file():
        return False, "Dashboard build completed without producing the interface."
    stamps.write(root / stamps.BUILD_STAMP, stamps.dashboard_digest(root))
    return True, "Dashboard built."
