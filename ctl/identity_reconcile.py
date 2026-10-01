"""Open Actual Budget to the household once its owner has signed in.

Actual makes the first person who signs in through Authentik its server owner,
so until the installing owner has done that, Authentik admits only them. Once
Actual's own database shows their OpenID session, the guard is lifted and
everyone in the household can sign in. Every other app is fully set up at
install and needs nothing here.
"""

from __future__ import annotations

import os
from pathlib import Path

from ctl import onboarding_state
from ctl.jobs import JobStore, redact
from ctl.lifecycle.accounts import actual_owner_linked
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

GUARD = "MU3LAB_INITIAL_OWNER_USERNAME"


def lift_owner_guard(_store: JobStore, _root: Path, log, paths: RuntimePaths = RuntimePaths()) -> bool:
    """Return True when the guard was lifted on this pass."""
    env_path = paths.projects / "actual-budget" / ".env"
    values = read_runtime_env(env_path)
    if not values.get(GUARD):
        return False
    try:
        owner = onboarding_state.read("actual-budget", paths).get("owner")
        if not owner or not actual_owner_linked(owner, paths):
            return False
        values[GUARD] = ""
        env_path.write_text(runtime_env_text(values), encoding="utf-8")
        os.chmod(env_path, 0o600)
        from ctl.identity import reconcile_blueprints
        from ctl.service_state import tailnet_dns_name

        reconcile_blueprints(load(), tailnet_dns_name(), paths)
        from ctl.authentik_apply import apply_blueprints

        apply_blueprints(log)
    except (OSError, ValueError) as exc:
        log(f"Actual Budget household access deferred: {redact(str(exc))}")
        return False
    log("Actual Budget is now open to the whole household.")
    return True
