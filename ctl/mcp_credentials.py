"""Automatic, app-native credential registration for reviewed MCP servers.

Plaintext credentials are captured once from an application's supported model,
written to the existing mode-0600 MCP environment, and never logged.  Account
selection deliberately fails when more than one plausible owner exists.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from ctl import actions, mcp_config
from ctl.mcp_registry import missing_credentials
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

MARKER = "MU3LAB_MCP_CREDENTIAL="
LABEL = "Mu3Lab MCP"


def _project(service_id: str) -> Path:
    return RuntimePaths().projects / service_id


def _secret(output: str) -> str:
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            value = line.removeprefix(MARKER).strip()
            if value and "\n" not in value and "\r" not in value:
                return value
    raise ValueError("the application did not return the generated credential")


def _exec(service_id: str, compose_service: str, argv: list[str], log) -> str:
    rc, output = actions.compose_exec(_project(service_id), compose_service, argv, log, timeout=90)
    if rc:
        if "expected exactly one" in output:
            raise ValueError(
                f"{service_id} needs exactly one eligible owner or administrator before automatic MCP registration"
            )
        # Application command output can contain account identifiers. Keep the
        # operator error useful without copying arbitrary output into job logs.
        raise ValueError(f"{service_id} rejected automatic MCP credential registration")
    return output


def _paperless(log) -> dict[str, str]:
    script = """
from rest_framework.authtoken.models import Token
from django.contrib.auth import get_user_model
users = list(get_user_model().objects.filter(is_active=True, is_superuser=True)[:2])
if len(users) != 1:
    raise RuntimeError('expected exactly one active superuser')
token, _ = Token.objects.get_or_create(user=users[0])
print('MU3LAB_MCP_CREDENTIAL=' + token.key)
""".strip()
    return {
        "api_key": _secret(_exec("paperless-ngx", "webserver", ["python", "manage.py", "shell", "-c", script], log))
    }


def _adventurelog(log) -> dict[str, str]:
    script = """
from users.models import APIKey, CustomUser
users = list(CustomUser.objects.filter(is_active=True, is_superuser=True)[:2])
if len(users) != 1:
    raise RuntimeError('expected exactly one active superuser')
APIKey.objects.filter(user=users[0], name='Mu3Lab MCP').delete()
_, raw = APIKey.generate(users[0], 'Mu3Lab MCP')
print('MU3LAB_MCP_CREDENTIAL=' + raw)
""".strip()
    return {"api_key": _secret(_exec("adventurelog", "app", ["python", "manage.py", "shell", "-c", script], log))}


def _mealie(log) -> dict[str, str]:
    script = """
from datetime import timedelta
from sqlalchemy import select
from mealie.core.security import create_access_token
from mealie.db.db_setup import session_context
from mealie.db.models.users.users import LongLiveToken, User
with session_context() as db:
    users = list(db.execute(select(User).where(User.admin == True).limit(2)).scalars())
    if len(users) != 1:
        raise RuntimeError('expected exactly one administrator')
    user = users[0]
    db.query(LongLiveToken).filter(LongLiveToken.user_id == user.id, LongLiveToken.name == 'Mu3Lab MCP').delete()
    token = create_access_token({'long_token': True, 'id': str(user.id), 'name': 'Mu3Lab MCP'}, timedelta(days=1825))
    db.add(LongLiveToken(name='Mu3Lab MCP', token=token, user_id=user.id))
    db.commit()
    print('MU3LAB_MCP_CREDENTIAL=' + token)
""".strip()
    return {"api_token": _secret(_exec("mealie", "mealie", ["/opt/mealie/bin/python", "-c", script], log))}


def _surfsense(log) -> dict[str, str]:
    script = """
import asyncio
from sqlalchemy import delete, select, update
from app.db import PersonalAccessToken, User, Workspace, async_session_maker
from app.utils.pat import generate_pat, hash_pat, token_prefix
async def main():
    async with async_session_maker() as db:
        users = list((await db.execute(select(User).where(User.is_active == True).limit(2))).scalars())
        if len(users) != 1:
            raise RuntimeError('expected exactly one active user')
        user = users[0]
        await db.execute(delete(PersonalAccessToken).where(PersonalAccessToken.user_id == user.id, PersonalAccessToken.label == 'Mu3Lab MCP'))
        token = generate_pat()
        db.add(PersonalAccessToken(user_id=user.id, token_hash=hash_pat(token), token_prefix=token_prefix(token), label='Mu3Lab MCP', expires_at=None))
        await db.execute(update(Workspace).where(Workspace.user_id == user.id).values(api_access_enabled=True))
        await db.commit()
        print('MU3LAB_MCP_CREDENTIAL=' + token)
asyncio.run(main())
""".strip()
    return {"api_token": _secret(_exec("surfsense", "backend", ["python", "-c", script], log))}


def _nextcloud(log) -> dict[str, str]:
    prefix = ["runuser", "-u", "www-data", "--", "php", "occ"]
    output = _exec("nextcloud", "app", [*prefix, "group:list", "--output=json"], log)
    try:
        admins = json.loads(output).get("admin", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("Nextcloud did not return its administrator list") from exc
    if len(admins) != 1 or not isinstance(admins[0], str):
        raise ValueError("Nextcloud needs exactly one administrator for automatic MCP registration")
    username = admins[0]
    tokens = _exec("nextcloud", "app", [*prefix, "user:auth-tokens:list", username, "--output=json"], log)
    try:
        for token in json.loads(tokens):
            if token.get("name") == LABEL:
                _exec(
                    "nextcloud",
                    "app",
                    [*prefix, "user:auth-tokens:delete", "--no-interaction", username, str(token["id"])],
                    log,
                )
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("Nextcloud did not return its app-password list") from exc
    output = _exec(
        "nextcloud", "app", [*prefix, "user:auth-tokens:add", "--no-interaction", f"--name={LABEL}", username], log
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"[A-Za-z0-9]{40,}", lines[-1]):
        raise ValueError("Nextcloud did not return the generated app password")
    return {"username": username, "app_password": lines[-1]}


def _immich(log) -> dict[str, str]:
    project = _project("immich")
    values = read_runtime_env(project / ".env")
    db_user = values.get("DB_USERNAME", "postgres")
    db_name = values.get("DB_DATABASE_NAME", "immich")
    if not re.fullmatch(r"[A-Za-z0-9_]+", db_user) or not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
        raise ValueError("Immich database identifiers are invalid")
    rc, output = actions.compose_exec(
        project,
        "database",
        [
            "psql",
            "-U",
            db_user,
            "-d",
            db_name,
            "-Atc",
            'select id from "user" where "isAdmin" is true and "deletedAt" is null and status=\'active\' limit 2;',
        ],
        log,
        timeout=60,
    )
    admins = [line.strip() for line in output.splitlines() if line.strip()] if rc == 0 else []
    if len(admins) != 1 or not re.fullmatch(r"[0-9a-f-]{36}", admins[0]):
        raise ValueError("Immich needs exactly one active administrator for automatic MCP registration")
    token = secrets.token_urlsafe(32).replace("-", "").replace("_", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    sql = f"""BEGIN;
DELETE FROM api_key WHERE "userId" = '{admins[0]}'::uuid AND name = '{LABEL}';
INSERT INTO api_key (id, name, key, "userId", permissions, "createdAt", "updatedAt", "updateId")
VALUES (gen_random_uuid(), '{LABEL}', decode('{digest}', 'hex'), '{admins[0]}'::uuid, ARRAY['all']::varchar[], now(), now(), gen_random_uuid());
COMMIT;
"""
    command = [
        "docker",
        "compose",
        "-f",
        str(project / "docker-compose.yml"),
        "--project-directory",
        str(project),
        "exec",
        "-T",
        "database",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        db_user,
        "-d",
        db_name,
    ]
    rc, _ = actions.docker_cmd_stdin(command, sql, log, timeout=60)
    if rc:
        raise ValueError("Immich rejected automatic MCP API-key registration")
    return {"api_key": token}


PROVISIONERS = {
    "paperless-community": _paperless,
    "adventurelog": _adventurelog,
    "mealie-community": _mealie,
    "surfsense-official": _surfsense,
    "nextcloud-context-agent": _nextcloud,
    "immich-community": _immich,
}


def ensure(server, log) -> tuple[bool, str]:
    """Create missing app credentials once and persist them through mcp_config."""
    missing = missing_credentials(server)
    if not missing:
        return True, "MCP credentials are already registered."
    if not server.auto_provision or server.id not in PROVISIONERS:
        return False, server.auto_provision_note or "This application requires manual MCP credentials."
    try:
        submitted = PROVISIONERS[server.id](log)
        mcp_config.write(server, submitted)
    except (OSError, ValueError, TypeError) as exc:
        return False, str(exc)
    return True, f"Created and securely registered a dedicated {server.name} credential."
