"""Nextcloud first-run installation and Authentik/Calendar configuration."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.jobs import redact
from ctl.secrets import read_runtime_env, runtime_env_text

Log = Callable[[str], None]
OCC = ["runuser", "-u", "www-data", "--", "php", "occ"]
# The entrypoint holds a flock on this file while it copies and installs, and
# never deletes it, so the file existing says nothing: only a held lock does.
INIT_LOCK = "/var/www/html/nextcloud-init-sync.lock"
INIT_LOCK_IDLE = ["sh", "-c", f"test ! -e {INIT_LOCK} || flock -n {INIT_LOCK} true"]
REQUIRED_APPS = ("calendar", "user_oidc")


def nextcloud_installed(project: Path, log: Log) -> bool:
    """Ask Nextcloud itself; config.php exists before installation commits."""
    rc, output = actions.compose_exec(project, "app", [*OCC, "status", "--output=json"], log, timeout=90)
    if rc:
        return False
    try:
        start = output.index("{")
        return bool(json.loads(output[start:]).get("installed", False))
    except (ValueError, json.JSONDecodeError, TypeError):
        return False


def install_nextcloud_if_needed(project: Path, log: Log) -> tuple[bool, str]:
    """Complete a fresh bootstrap explicitly when Docker auto-install did not.

    The reviewed bootstrap override injects NEXTCLOUD_ADMIN_* into the running
    app. Variable names, rather than secret values, are used in the command so
    job events cannot disclose credentials.
    """
    if nextcloud_installed(project, log):
        return True, "Nextcloud base installation is already complete."
    # The image's entrypoint copies Nextcloud into the volume and, because the
    # bootstrap override sets NEXTCLOUD_ADMIN_*, installs it itself while
    # holding this lock. A second `maintenance:install` racing it creates the
    # tables under two different database roles and fails with "permission
    # denied for table oc_migrations". Wait for the entrypoint to finish and
    # only install explicitly when it did not. Compose's health wait cannot be
    # used: the app health check requires an installed instance.
    deadline = time.monotonic() + 600
    last = "Nextcloud occ is not ready yet."
    while time.monotonic() < deadline:
        rc, _output = actions.compose_exec(project, "app", INIT_LOCK_IDLE, log, timeout=30)
        if rc != 0:
            last = "The Nextcloud container is still initializing."
            time.sleep(3)
            continue
        rc, output = actions.compose_exec(project, "app", [*OCC, "status", "--output=json"], log, timeout=30)
        if rc == 0:
            break
        last = redact(output) or last
        time.sleep(2)
    else:
        return False, "Nextcloud did not become ready for first-run installation: " + last
    if nextcloud_installed(project, log):
        return True, "Nextcloud base installation completed during container start."
    script = (
        "set -eu; "
        'test -n "${NEXTCLOUD_ADMIN_USER:-}"; '
        'test -n "${NEXTCLOUD_ADMIN_PASSWORD:-}"; '
        "runuser -u www-data -- php occ maintenance:install "
        "--database pgsql --database-host db --database-name nextcloud "
        '--database-user nextcloud --database-pass="$POSTGRES_PASSWORD" '
        '--admin-user="$NEXTCLOUD_ADMIN_USER" --admin-pass="$NEXTCLOUD_ADMIN_PASSWORD"'
    )
    rc, output = actions.compose_exec(project, "app", ["sh", "-ec", script], log, timeout=300)
    if rc and "already installed" not in output.lower():
        return False, "Nextcloud base installation failed: " + redact(output)
    if not nextcloud_installed(project, log):
        return False, "Nextcloud did not confirm a completed base installation."
    return True, "Nextcloud base installation completed."


def configure_nextcloud(project: Path, log: Log) -> tuple[bool, str]:
    """Install the latest Nextcloud-compatible apps and configure Authentik.

    `occ app:install` resolves the current app-store release compatible with
    the installed server, so versions are reported, never compared against a
    hard-coded expectation.
    """
    env = read_runtime_env(project / ".env")
    client_id = env.get("NEXTCLOUD_OIDC_CLIENT_ID", "")
    client_secret = env.get("NEXTCLOUD_OIDC_CLIENT_SECRET", "")
    host = env.get("NEXTCLOUD_OVERWRITEHOST", "").split(":", 1)[0]
    if not client_id or not client_secret or not host:
        return False, "Nextcloud OIDC runtime values are incomplete."
    for app_id in REQUIRED_APPS:
        rc, output = actions.compose_exec(project, "app", [*OCC, "app:install", app_id], log, timeout=300)
        if rc and "already installed" not in output.lower():
            return False, f"Nextcloud could not install {app_id}: {redact(output)}"
        rc, output = actions.compose_exec(project, "app", [*OCC, "app:enable", app_id], log, timeout=120)
        if rc:
            return False, f"Nextcloud could not enable {app_id}: {redact(output)}"
    rc, output = actions.compose_exec(project, "app", [*OCC, "app:list", "--output=json"], log, timeout=120)
    try:
        app_state = json.loads(output[output.index("{") :]) if rc == 0 else {}
    except (ValueError, json.JSONDecodeError):
        app_state = {}
    enabled = app_state.get("enabled", {}) if isinstance(app_state, dict) else {}
    missing = [app_id for app_id in REQUIRED_APPS if not str(enabled.get(app_id, ""))]
    if missing:
        return False, "Nextcloud required app(s) are not enabled: " + ", ".join(missing)
    versions = ", ".join(f"{app_id} {enabled[app_id]}" for app_id in REQUIRED_APPS)
    if "firstrunwizard" in enabled:
        rc, output = actions.compose_exec(project, "app", [*OCC, "app:disable", "firstrunwizard"], log, timeout=120)
        if rc:
            return False, "Nextcloud could not skip its first-run tour: " + redact(output)
    discovery = f"https://{host}/application/o/mu3lab-nextcloud/.well-known/openid-configuration"
    command = [
        *OCC,
        "user_oidc:provider",
        "mu3lab",
        f"--clientid={client_id}",
        f"--clientsecret={client_secret}",
        f"--discoveryuri={discovery}",
        "--mapping-uid=preferred_username",
        "--unique-uid=0",
    ]
    rc, output = actions.compose_exec(project, "app", command, log, timeout=120)
    if rc:
        return False, "Nextcloud could not configure its Authentik provider: " + redact(output)
    rc, output = actions.compose_exec(project, "app", [*OCC, "user_oidc:provider", "mu3lab"], log, timeout=120)
    if rc or client_id not in output:
        return False, "Nextcloud did not confirm the Authentik provider configuration."
    rc, output = actions.compose_exec(project, "app", [*OCC, "user_oidc:providers", "--output=json"], log, timeout=120)
    provider_id = None
    if rc == 0:
        for line in output.splitlines():
            try:
                provider = json.loads(line)
            except (ValueError, TypeError):
                continue
            if provider.get("identifier") == "mu3lab":
                provider_id = provider.get("id")
    if not str(provider_id).isdigit() or int(str(provider_id)) < 1:
        return False, "Nextcloud did not report its managed OIDC provider ID."
    env["NEXTCLOUD_OIDC_PROVIDER_ID"] = str(provider_id)
    (project / ".env").write_text(runtime_env_text(env), encoding="utf-8")
    os.chmod(project / ".env", 0o600)
    for key in ("auto_provision", "soft_auto_provision"):
        rc, output = actions.compose_exec(
            project,
            "app",
            [*OCC, "config:system:set", "user_oidc", key, "--type=boolean", "--value=true"],
            log,
            timeout=120,
        )
        if rc:
            return False, f"Nextcloud could not enable {key}: " + redact(output)
    # Local login stays available until a real Authentik callback proves the
    # subject resolves to the existing administrator.
    rc, output = actions.compose_exec(
        project,
        "app",
        [*OCC, "config:app:set", "user_oidc", "allow_multiple_user_backends", "--value=1"],
        log,
        timeout=120,
    )
    if rc:
        return False, "Nextcloud could not stage its safe OIDC migration policy: " + redact(output)
    # The tailnet hostname resolves to a CGNAT address, which Nextcloud's DNS
    # pinning otherwise rejects as a local server during OIDC discovery.
    rc, output = actions.compose_exec(
        project,
        "app",
        [*OCC, "config:system:set", "allow_local_remote_servers", "--type=boolean", "--value=true"],
        log,
        timeout=120,
    )
    if rc:
        return False, "Nextcloud could not permit its private Authentik discovery route: " + redact(output)
    return (
        True,
        f"Latest compatible Nextcloud apps enabled ({versions}); Calendar and Authentik sign-in are configured.",
    )
