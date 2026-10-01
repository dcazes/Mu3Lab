"""Application administrator accounts for Authentik-only apps.

Evidence is read from each application's own data model. Password hashes and
raw identity data never enter job logs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
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
        f"u=get_user_model().objects.filter(username={expected_username!r} or os.environ.get('{variable}','')).first(); "
        "print('MU3LAB_ACCOUNT_OK' if u and u.is_superuser else 'MU3LAB_ACCOUNT_MISSING')"
    )
    rc, output = actions.compose_exec(project, container, ["python", "manage.py", "shell", "-c", code], log, timeout=90)
    return rc == 0 and "MU3LAB_ACCOUNT_OK" in output, output


def actual_owner_linked(owner: Mapping[str, object], paths: RuntimePaths = RuntimePaths()) -> bool:
    """Whether the installing owner has signed in to Actual through Authentik and owns it."""
    database = paths.data / "actual-budget" / "server-files" / "account.sqlite"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute(
                "SELECT 1 FROM users u JOIN sessions s ON s.user_id=u.id "
                "WHERE u.user_name=? AND u.enabled=1 AND u.owner=1 AND u.role='ADMIN' "
                "AND s.auth_method='openid' AND (s.expires_at=-1 OR s.expires_at>?) LIMIT 1",
                (str(owner.get("username", "")), int(time.time())),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return False
    return bool(row)


# Settings an app can only switch to after install. Every other Authentik-only
# setting is written before the app's first start (see materialize.SSO_ONLY).
SSO_ONLY_SETTINGS: dict[str, dict[str, str]] = {
    # Immich's administrator is created through its password API.
    "immich": {"IMMICH_PASSWORD_LOGIN_ENABLED": "false"},
}


def enforce_identity_settings(service: Service, project: Path) -> bool:
    """Apply post-install Authentik-only settings; True when anything changed."""
    settings = SSO_ONLY_SETTINGS.get(service.id)
    if not settings:
        return False
    before = read_runtime_env(project / ".env")
    values = before | settings
    (project / ".env").write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(project / ".env", 0o600)
    if service.id == "immich":
        path = project / "immich-config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["passwordLogin"] = {"enabled": False}
        config["oauth"]["autoLaunch"] = True
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    return before != values
