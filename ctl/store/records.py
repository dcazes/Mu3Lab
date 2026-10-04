"""Non-secret runtime records stored in the shared database."""

from __future__ import annotations

import json
import time
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import db


def get(scope: str, name: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, Any]:
    path = db.database(paths)
    if not path.is_file():
        return {}
    with db.connect(path, readonly=True) as connection:
        row = connection.execute("SELECT value_json FROM records WHERE scope=? AND name=?", (scope, name)).fetchone()
    return json.loads(row[0]) if row else {}


def put(scope: str, name: str, value: dict[str, Any], paths: RuntimePaths = RuntimePaths()) -> None:
    with db.connect(db.database(paths)) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(scope, name) DO UPDATE SET "
            "value_json=excluded.value_json, updated_at=excluded.updated_at",
            (scope, name, json.dumps(value), time.time()),
        )
