"""Logins Mu3Lab created for each person in an app without single sign-on.

Apps such as Unsloth Studio and Navidrome keep their own passwords: Studio for
its web app, Navidrome for phone apps. Mu3Lab creates each person's account
with a generated password, keeps it here encrypted, and saves it to that
person's vault (``ctl.vault_sync``), where Bitwarden fills it in. The copy
stays while the account is open, so the vault can be filled again later and a
closed account (Unsloth Studio) can reopen with the same password.
"""

from __future__ import annotations

from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore


def _scope(app_id: str) -> str:
    return "app:" + app_id


def _name(username: str) -> str:
    return "login:" + username


def get(app_id: str, username: str, paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    record = SecretStore(paths or RuntimePaths()).get(_scope(app_id), _name(username))
    return record if isinstance(record, dict) and record.get("password") else None


def put(app_id: str, username: str, login: str, password: str, paths: RuntimePaths | None = None) -> None:
    """``login`` is the name the app knows the person by; usually their Authentik username."""
    SecretStore(paths or RuntimePaths()).put(_scope(app_id), _name(username), {"login": login, "password": password})


def forget(app_id: str, username: str, paths: RuntimePaths | None = None) -> None:
    SecretStore(paths or RuntimePaths()).delete(_scope(app_id), _name(username))
