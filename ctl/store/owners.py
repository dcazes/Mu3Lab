"""The authoritative app owner; subsequent installers cannot replace them."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ctl.store import db


def get(service_id: str, database: Path) -> dict[str, str]:
    if not database.is_file():
        return {}
    with db.connect(database, readonly=True) as connection:
        row = connection.execute(
            "SELECT owner_uid, username, email, display_name FROM app_owner WHERE service_id=?", (service_id,)
        ).fetchone()
    return dict(row) if row else {}


def remember(service_id: str, identity: Mapping[str, object], database: Path) -> dict[str, str]:
    uid = str(identity.get("owner_uid") or "")
    if not uid:
        return get(service_id, database)
    values = [str(identity.get(key) or "") for key in ("username", "email", "display_name")]
    with db.connect(database) as connection:
        connection.execute(
            "INSERT INTO app_owner VALUES (?, ?, ?, ?, ?) ON CONFLICT(service_id) DO UPDATE SET "
            "username=CASE WHEN app_owner.username='' THEN excluded.username ELSE app_owner.username END, "
            "email=CASE WHEN app_owner.email='' THEN excluded.email ELSE app_owner.email END, "
            "display_name=CASE WHEN app_owner.display_name='' THEN excluded.display_name ELSE app_owner.display_name END "
            "WHERE app_owner.owner_uid=excluded.owner_uid",
            (service_id, uid, *values),
        )
    return get(service_id, database)


def forget(service_id: str, database: Path) -> None:
    if database.is_file():
        with db.connect(database) as connection:
            connection.execute("DELETE FROM app_owner WHERE service_id=?", (service_id,))
