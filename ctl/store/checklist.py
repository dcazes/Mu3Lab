"""Each person's checklist, merged by item so devices cannot erase other ticks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from ctl.runtime import RuntimePaths
from ctl.store import db

ITEMS = frozenset({"devices", "extension", "chat", "hidden"})


def read(uid: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, bool]:
    if not db.database(paths).is_file():
        return {}
    with db.connect(db.database(paths)) as connection:
        return {
            row[0]: True for row in connection.execute("SELECT item FROM person_checklist WHERE person_uid=?", (uid,))
        }


def update(uid: str, items: Mapping[str, bool], paths: RuntimePaths = RuntimePaths()) -> dict[str, bool]:
    if not uid or set(items) - ITEMS:
        raise ValueError("A verified person and known checklist items are required.")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with db.connect(db.database(paths)) as connection:
        for item, complete in items.items():
            if complete:
                connection.execute(
                    "INSERT INTO person_checklist VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (uid, item, now)
                )
            else:
                connection.execute("DELETE FROM person_checklist WHERE person_uid=? AND item=?", (uid, item))
    return read(uid, paths)
