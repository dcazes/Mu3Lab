"""Small persistent record of human-completed bootstrap setup steps.

Only boolean acknowledgements and timestamps are stored. Passwords, URLs with
credentials, and application data never enter this file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ctl.runtime import RuntimePaths

STATE_NAME = "bootstrap-state.json"


def path(paths: RuntimePaths = RuntimePaths()) -> Path:
    return paths.runtime / STATE_NAME


def read(paths: RuntimePaths = RuntimePaths()) -> dict[str, object]:
    try:
        value = json.loads(path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def is_complete(step: str, paths: RuntimePaths = RuntimePaths(), inputs: dict | None = None) -> bool:
    return bool((inputs or {}).get(f"{step}_confirmed") or read(paths).get(step))


def confirm(step: str, paths: RuntimePaths = RuntimePaths()) -> None:
    target = path(paths)
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    values = read(paths)
    values[step] = {"confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
