"""Small persistent record of human-completed bootstrap setup steps.

Only boolean acknowledgements and timestamps are stored. Passwords, URLs with
credentials, and application data never enter this file.
"""

from __future__ import annotations

import time

from ctl.runtime import RuntimePaths
from ctl.secret_file import locked
from ctl.store import records


def read(paths: RuntimePaths = RuntimePaths()) -> dict[str, object]:
    return records.get("bootstrap", "steps", paths)


def is_complete(step: str, paths: RuntimePaths = RuntimePaths(), inputs: dict | None = None) -> bool:
    return bool((inputs or {}).get(f"{step}_confirmed") or read(paths).get(step))


def confirm(step: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with locked(paths.state / "bootstrap.lock"):
        values = read(paths)
        values[step] = {"confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        records.put("bootstrap", "steps", values, paths)
