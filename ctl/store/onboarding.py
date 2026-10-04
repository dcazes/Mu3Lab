"""Owner mappings and pending generated logins, encrypted outside the checkout.

Unlike expiring job inputs, these survive a restart or a delayed first login.
Each app has its own record; a lock protects key creation and read/modify/write.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ctl.runtime import RuntimePaths
from ctl.store import db, owners
from ctl.store.secrets import SecretStore
from ctl.store.workflows import JobIdentity, WorkflowSecretError, generate_password

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
    return db.database(paths)


def _read(service_id: str, paths: RuntimePaths) -> dict:
    record = SecretStore(paths).get("app:" + service_id, "onboarding") or {}
    owner = owners.get(service_id, db.database(paths))
    if owner:
        record["owner"] = owner
    return record


def _write(service_id: str, record: dict, paths: RuntimePaths) -> None:
    data = dict(record)
    owner = data.pop("owner", None)
    if owner:
        owners.remember(service_id, owner, db.database(paths))
    SecretStore(paths).put("app:" + service_id, "onboarding", data)


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
            existing = owners.remember(service_id, owner, db.database(paths))
            return JobIdentity(
                owner_uid=existing["owner_uid"],
                username=existing["username"],
                email=existing["email"],
                display_name=existing["display_name"],
            )
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
    if not db.database(paths).is_file():
        return []
    with _locked(paths):
        result = []
        with db.connect(db.database(paths), readonly=True) as connection:
            service_ids = [
                row[0] for row in connection.execute("SELECT service_id FROM app_owner WHERE owner_uid=?", (owner_uid,))
            ]
        for service_id in service_ids:
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


def discard_password(service_id: str, paths: RuntimePaths = RuntimePaths()) -> None:
    """Drop an Authentik-only app's generated admin password; nobody signs in with it."""
    with _locked(paths):
        record = _read(service_id, paths)
        if record.get("password"):
            record.pop("password", None)
            _write(service_id, record, paths)


def forget(service_id: str, paths: RuntimePaths = RuntimePaths()) -> None:
    with _locked(paths):
        SecretStore(paths).delete("app:" + service_id, "onboarding")
        owners.forget(service_id, db.database(paths))


def mark_configured(service_id: str, paths: RuntimePaths = RuntimePaths()) -> None:
    """Avoid reapplying an already-current definition on every dashboard visit."""
    with _locked(paths):
        record = _read(service_id, paths)
        if record.get("owner"):
            record["config_version"] = CONFIG_VERSION
            _write(service_id, record, paths)
