"""Owner-scoped, encrypted Nextcloud calendar credentials."""

from __future__ import annotations

from ctl.runtime import RuntimePaths
from ctl.secret_file import serialized
from ctl.store.secrets import SecretError as CalendarSecretError
from ctl.store.secrets import SecretStore


def _read(paths: RuntimePaths) -> dict[str, dict[str, str]]:
    store = SecretStore(paths)
    return {name: store.get("calendar", name) for name in store.list_names("calendar")}


def _write(records: dict[str, dict[str, str]], paths: RuntimePaths) -> None:
    store = SecretStore(paths)
    for name in set(store.list_names("calendar")) - set(records):
        store.delete("calendar", name)
    for name, record in records.items():
        store.put("calendar", name, record)


@serialized("calendar-secrets.lock")
def save(owner_uid: str, username: str, app_password: str, paths: RuntimePaths = RuntimePaths()) -> None:
    if not owner_uid or len(owner_uid) > 256 or not username or len(username) > 256:
        raise CalendarSecretError("valid calendar owner and username are required")
    if not app_password or len(app_password) > 4096:
        raise CalendarSecretError("a valid Nextcloud app password is required")
    records = _read(paths)
    records[owner_uid] = {"username": username, "app_password": app_password}
    _write(records, paths)


@serialized("calendar-secrets.lock")
def get(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str] | None:
    value = _read(paths).get(owner_uid)
    return dict(value) if isinstance(value, dict) else None


@serialized("calendar-secrets.lock")
def delete(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> None:
    records = _read(paths)
    if owner_uid in records:
        del records[owner_uid]
        _write(records, paths)
