"""Shared observations; reading them never runs Docker or checks a route."""

from __future__ import annotations

import json
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import db


def read(paths: RuntimePaths = RuntimePaths()) -> dict[str, dict[str, Any]]:
    path = db.database(paths)
    if not path.is_file():
        return {}
    with db.connect(path) as connection:
        rows = connection.execute("SELECT service_id, projection_json, observed_at FROM status_snapshot").fetchall()
    return {row["service_id"]: json.loads(row["projection_json"]) | {"observed_at": row["observed_at"]} for row in rows}


def write(projections: list[dict[str, Any]], observed_at: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with db.connect(db.database(paths)) as connection:
        for projection in projections:
            connection.execute(
                "INSERT INTO status_snapshot VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(service_id) DO UPDATE SET health=excluded.health, detail=excluded.detail, "
                "containers_json=excluded.containers_json, route_ok=excluded.route_ok, "
                "observed_at=excluded.observed_at, projection_json=excluded.projection_json",
                (
                    projection["id"],
                    projection["health_state"],
                    projection["detail"],
                    json.dumps(projection["containers"]),
                    int(projection["route_ready"]),
                    observed_at,
                    json.dumps(projection),
                ),
            )
