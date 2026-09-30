"""Owner mappings and pending generated logins, encrypted outside the checkout.

Unlike expiring job inputs, these survive a restart or a delayed first login.
Each app has its own record; a lock protects key creation and read/modify/write.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ctl.runtime import RuntimePaths
from ctl.workflow_secrets import JobIdentity, WorkflowSecretError, generate_password

CONFIG_VERSION = 1


@contextmanager
def _locked(paths: RuntimePaths) -> Iterator[None]:
    paths.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (paths.runtime / "onboarding.lock").open("a") as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _path(service_id: str, paths: RuntimePaths) -> Path:
    if not service_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in service_id):
        raise ValueError("invalid onboarding application")
    return paths.runtime / f"onboarding-{service_id}.enc"


def _cipher(paths: RuntimePaths):
    from cryptography.fernet import Fernet

    key = paths.runtime / "onboarding.key"
    if not key.exists():
        with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(Fernet.generate_key())
    return Fernet(key.read_bytes())


def _read(service_id: str, paths: RuntimePaths) -> dict:
    path = _path(service_id, paths)
    if not path.is_file():
        return {}
    try:
        record = json.loads(_cipher(paths).decrypt(path.read_bytes()))
        if not isinstance(record, dict):
            raise ValueError("invalid onboarding record")
        return record
    except Exception as exc:
        raise WorkflowSecretError("Application onboarding storage could not be read.") from exc


def _write(service_id: str, record: dict, paths: RuntimePaths) -> None:
    path = _path(service_id, paths)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_cipher(paths).encrypt(json.dumps(record).encode()))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read(service_id: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    if not _path(service_id, paths).is_file():
        return {}
    with _locked(paths):
        return _read(service_id, paths)


def remember_owner(service_id: str, owner: Mapping[str, object], paths: RuntimePaths = RuntimePaths()) -> JobIdentity:
    if not owner.get("owner_uid") or not owner.get("email") or not owner.get("username"):
        raise WorkflowSecretError("A verified application owner is required.")
    with _locked(paths):
        record = _read(service_id, paths)
        if record.get("owner"):
            # Reinstall/repair by a second operator must not replace ownership.
            return record["owner"]
        identity: JobIdentity = {
            "owner_uid": str(owner["owner_uid"]),
            "username": str(owner["username"]),
            "email": str(owner["email"]),
            "display_name": str(owner.get("display_name", "")),
        }
        record["owner"] = identity
        _write(service_id, record, paths)
        return identity


def prepare_login(service_id: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    """Persist before provisioning so a crash never loses the generated password."""
    with _locked(paths):
        record = _read(service_id, paths)
        if not record.get("owner"):
            raise WorkflowSecretError("Application owner is missing.")
        if not record.get("password"):
            if record.get("vault_saved"):
                raise WorkflowSecretError("This application's existing login is already saved in the vault.")
            record["password"] = generate_password()
            _write(service_id, record, paths)
        return record


def complete_login(service_id: str, username: str, login_url: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with _locked(paths):
        record = _read(service_id, paths)
        record.update(provisioned=True, login_username=username, login_url=login_url)
        _write(service_id, record, paths)


def pending_logins(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> list[dict]:
    if not paths.runtime.is_dir():
        return []
    with _locked(paths):
        result = []
        for path in paths.runtime.glob("onboarding-*.enc"):
            service_id = path.stem.removeprefix("onboarding-")
            record = _read(service_id, paths)
            if (
                record.get("owner", {}).get("owner_uid") == owner_uid
                and record.get("provisioned")
                and record.get("password")
            ):
                result.append(dict(record, service_id=service_id))
        return result


def vault_saved(service_id: str, owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with _locked(paths):
        record = _read(service_id, paths)
        if record.get("owner", {}).get("owner_uid") != owner_uid:
            raise WorkflowSecretError("Application login belongs to another owner.")
        record.pop("password", None)
        record["vault_saved"] = True
        _write(service_id, record, paths)


def forget(service_id: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with _locked(paths):
        _path(service_id, paths).unlink(missing_ok=True)


def mark_configured(service_id: str, paths: RuntimePaths = RuntimePaths()) -> None:
    """Avoid reapplying an already-current definition on every dashboard visit."""
    with _locked(paths):
        record = _read(service_id, paths)
        if record.get("owner"):
            record["config_version"] = CONFIG_VERSION
            _write(service_id, record, paths)
