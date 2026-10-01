"""Apply Mu3Lab's Authentik Blueprints now, in order, instead of whenever Authentik gets to them.

Authentik's worker watches the Blueprint folder and applies changed files in
the background, in no particular order, and keeps a record of every file it
has seen, even after the file is deleted. Uninstalling an app writes a
"removed" Blueprint; reinstalling deletes it again. Left to the watcher, an
old removal could run after the reinstall and delete the app's sign-in again.

This runs inside Authentik: it forgets records whose file is gone, then
applies removals before everything else, so the files on disk are the truth.
The watcher still applies the same files later; they are idempotent.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ctl import actions
from ctl.jobs import redact
from ctl.runtime import RuntimePaths

# Blank in the test suite, which must never touch the host's live Authentik.
CONTAINER = os.environ.get("MU3LAB_AUTHENTIK_CONTAINER", "authentik-server-1")
_MARK = "MU3LAB_BLUEPRINTS="
_SCRIPT = r"""
from pathlib import Path
from authentik.blueprints.models import BlueprintInstance
from authentik.blueprints.v1.importer import Importer

folder = Path("/blueprints/custom")
for instance in BlueprintInstance.objects.filter(path__startswith="custom/mu3lab-"):
    if not (Path("/blueprints") / instance.path).is_file():
        instance.delete()
files = sorted(folder.glob("mu3lab-*.yaml"), key=lambda path: (not path.stem.endswith("-removed"), path.name))
failed = []
for path in files:
    importer = Importer.from_string(path.read_text(encoding="utf-8"))
    valid, _logs = importer.validate()
    if not valid or not importer.apply():
        failed.append(path.name)
print("MU3LAB_BLUEPRINTS=" + (",".join(failed) if failed else "ok"))
"""


def apply_blueprints(log: Callable[[str], None]) -> tuple[bool, str]:
    """Apply every Mu3Lab Blueprint now; (ok, plain-language detail)."""
    if not CONTAINER or not (RuntimePaths().projects / "authentik" / "blueprints").is_dir():
        return True, "No Authentik sign-in settings to apply."
    rc, output = actions.docker_cmd_with_stdin(
        ["docker", "exec", "-i", CONTAINER, "ak", "shell"], _SCRIPT, lambda _line: None, timeout=180
    )
    line = next((line for line in reversed(output.splitlines()) if line.startswith(_MARK)), "")
    result = line.removeprefix(_MARK)
    if rc or not line:
        log("Authentik could not apply Mu3Lab's sign-in settings: " + redact(output[-400:]))
        return False, "Authentik did not apply Mu3Lab's sign-in settings."
    if result != "ok":
        return False, f"Authentik rejected Mu3Lab's sign-in settings for: {result}."
    return True, "Authentik applied Mu3Lab's sign-in settings."
