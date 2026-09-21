"""Owner-scoped, encrypted Nextcloud calendar credentials."""

from __future__ import annotations

import json
import os

from ctl.runtime import RuntimePaths


class CalendarSecretError(ValueError):
    pass


def _paths(paths: RuntimePaths):
    return paths.runtime / "calendar-secrets.key", paths.runtime / "calendar-connections.enc"


def _cipher(paths: RuntimePaths):
    from cryptography.fernet import Fernet
    key_path, _ = _paths(paths)
    paths.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if key_path.is_file():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
    return Fernet(key)


def _read(paths: RuntimePaths) -> dict[str, dict[str, str]]:
    _, target = _paths(paths)
    if not target.is_file():
        return {}
    try:
        value = json.loads(_cipher(paths).decrypt(target.read_bytes()).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - crypto details are intentionally hidden
        raise CalendarSecretError("encrypted calendar storage could not be read") from exc
    return value if isinstance(value, dict) else {}


def _write(records: dict[str, dict[str, str]], paths: RuntimePaths) -> None:
    _, target = _paths(paths)
    temporary = target.with_suffix(".enc.tmp")
    temporary.write_bytes(_cipher(paths).encrypt(json.dumps(records, sort_keys=True).encode("utf-8")))
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    os.chmod(target, 0o600)


def save(owner_uid: str, username: str, app_password: str,
         paths: RuntimePaths = RuntimePaths()) -> None:
    if not owner_uid or len(owner_uid) > 256 or not username or len(username) > 256:
        raise CalendarSecretError("valid calendar owner and username are required")
    if not app_password or len(app_password) > 4096:
        raise CalendarSecretError("a valid Nextcloud app password is required")
    records = _read(paths)
    records[owner_uid] = {"username": username, "app_password": app_password}
    _write(records, paths)


def get(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str] | None:
    value = _read(paths).get(owner_uid)
    return dict(value) if isinstance(value, dict) else None


def delete(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> None:
    records = _read(paths)
    if owner_uid in records:
        del records[owner_uid]
        _write(records, paths)
