"""Application administrator accounts and Authentik owner linking.

Evidence is read from each application's own data model. Password hashes and
raw identity data never enter job logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from ctl import actions
from ctl.lifecycle import nextcloud
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.workflow_secrets import JobIdentity

Log = Callable[[str], None]


def account_username(identity: JobIdentity) -> str:
    candidate = identity["username"] or identity["email"].split("@", 1)[0]
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:64]
    return value or "mu3lab-admin"


def fresh_account_storage(service_id: str, project: Path | None = None, log: Log | None = None) -> bool:
    data = RuntimePaths().data
    if service_id == "nextcloud":
        # config.php is written before installation commits; only Nextcloud's
        # own status command is a safe source of truth.
        return not (project is not None and log is not None and nextcloud.nextcloud_installed(project, log))
    directory = data / "paperless" / "postgres" if service_id == "paperless-ngx" else data / "adventurelog" / "postgres"
    try:
        return not directory.exists() or not any(directory.iterdir())
    except OSError:
        return False


def verify_bootstrap_account(service_id: str, project: Path, log: Log, expected_username: str = "") -> tuple[bool, str]:
    """Use the pinned application's own model layer to verify creation."""
    if service_id == "paperless-ngx":
        container, variable = "webserver", "PAPERLESS_ADMIN_USER"
    elif service_id == "adventurelog":
        container, variable = "app", "DJANGO_ADMIN_USERNAME"
    elif service_id == "nextcloud":
        rc, output = actions.compose_exec(
            project, "app", [*nextcloud.OCC, "user:list", "--output=json"], log, timeout=90
        )
        try:
            users = json.loads(output[output.index("{") :]) if rc == 0 else {}
        except (ValueError, json.JSONDecodeError):
            users = {}
        return rc == 0 and expected_username in users, output
    else:
        return True, "No automatic account verification required."
    code = (
        "import os; from django.contrib.auth import get_user_model; "
        f"u=get_user_model().objects.filter(username=os.environ.get('{variable}','')).first(); "
        "print('MU3LAB_ACCOUNT_OK' if u and u.is_superuser else 'MU3LAB_ACCOUNT_MISSING')"
    )
    rc, output = actions.compose_exec(project, container, ["python", "manage.py", "shell", "-c", code], log, timeout=90)
    return rc == 0 and "MU3LAB_ACCOUNT_OK" in output, output


def _nextcloud_owner_linked(project: Path, owner: Mapping[str, object], email: str, log: Log) -> bool:
    username = str(owner.get("username", "")).strip()
    if not username:
        return False
    rc, output = actions.compose_exec(
        project, "app", [*nextcloud.OCC, "user:info", username, "--output=json"], log, timeout=120
    )
    if rc:
        return False
    try:
        profile = json.loads(output[output.index("{") :])
    except (ValueError, json.JSONDecodeError):
        return False
    groups = {str(group) for group in profile.get("groups", [])}
    return (
        str(profile.get("user_id", "")) == username
        and str(profile.get("email", "")).strip().lower() == email
        and bool(profile.get("enabled"))
        and "admin" in groups
    )


def _mealie_owner_linked(email: str) -> bool:
    database = RuntimePaths().data / "mealie" / "mealie.db"
    try:
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        row = conn.execute("SELECT email, admin, auth_method FROM users WHERE lower(email) = ?", (email,)).fetchone()
        conn.close()
    except (OSError, sqlite3.Error):
        return False
    return bool(row and int(row[1]) == 1 and str(row[2]).upper() == "OIDC")


def _lobehub_owner_linked(project: Path, email: str, log: Log) -> bool:
    # Better Auth stores the linked provider separately from the user. Ask
    # PostgreSQL for only an irreversible email digest plus two booleans.
    expected_md5 = hashlib.md5(email.encode(), usedforsecurity=False).hexdigest()
    query = (
        "SELECT md5(lower(u.email)), u.email_verified, a.provider_id "
        "FROM users u JOIN accounts a ON a.user_id = u.id "
        "WHERE a.provider_id = 'authentik';"
    )
    rc, output = actions.compose_exec(
        project, "postgres", ["psql", "-U", "postgres", "-d", "lobehub", "-Atc", query], log, timeout=120
    )
    if rc:
        return False
    return any(
        parts[0] == expected_md5 and parts[1] == "t" and parts[2] == "authentik"
        for line in output.splitlines()
        if len(parts := line.strip().split("|")) == 3
    )


def _django_owner_linked(service_id: str, project: Path, email: str, log: Log) -> bool:
    container = "webserver" if service_id == "paperless-ngx" else "app"
    code = (
        "import hashlib; from allauth.socialaccount.models import SocialAccount; "
        "print('\\n'.join(hashlib.sha256((x.user.email or '').strip().lower().encode()).hexdigest() "
        "for x in SocialAccount.objects.select_related('user').all() "
        "if x.user.is_superuser and x.user.email))"
    )
    rc, output = actions.compose_exec(
        project, container, ["python", "manage.py", "shell", "-c", code], log, timeout=120
    )
    return rc == 0 and hashlib.sha256(email.encode()).hexdigest() in output.splitlines()


def linked_owner_verified(service: Service, project: Path, owner: Mapping[str, object] | None, log: Log) -> bool:
    """Whether the Authentik owner is linked to the app's administrator account."""
    email = str((owner or {}).get("email", "")).strip().lower()
    if not owner or not email:
        return False
    if service.id == "nextcloud":
        return _nextcloud_owner_linked(project, owner, email, log)
    if service.id == "mealie":
        return _mealie_owner_linked(email)
    if service.id == "lobehub":
        return _lobehub_owner_linked(project, email, log)
    if service.id in {"paperless-ngx", "adventurelog"}:
        return _django_owner_linked(service.id, project, email, log)
    return False


SSO_ONLY_SETTINGS: dict[str, dict[str, str]] = {
    "mealie": {
        "MEALIE_OIDC_AUTO_REDIRECT": "true",
        "MEALIE_ALLOW_PASSWORD_LOGIN": "false",
        "MEALIE_ALLOW_SIGNUP": "false",
    },
    "paperless-ngx": {"PAPERLESS_DISABLE_REGULAR_LOGIN": "true", "PAPERLESS_REDIRECT_LOGIN_TO_SSO": "true"},
    "adventurelog": {"ADVENTURELOG_FORCE_SOCIAL_LOGIN": "true"},
}


def enforce_identity_settings(service: Service, project: Path) -> None:
    """Switch an app to SSO-only sign-in once owner linking is verified."""
    settings = SSO_ONLY_SETTINGS.get(service.id)
    if not settings:
        return
    values = read_runtime_env(project / ".env") | settings
    (project / ".env").write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(project / ".env", 0o600)
