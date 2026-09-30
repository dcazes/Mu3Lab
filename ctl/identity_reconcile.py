"""Automatically finalize pending app identities after their first real login."""

from __future__ import annotations

from pathlib import Path

from ctl import onboarding_state
from ctl.control_state import ControlState
from ctl.identity import OIDC_CONTRACTS
from ctl.jobs import JobStore, redact
from ctl.lifecycle.accounts import linked_owner_verified
from ctl.registry import load
from ctl.runtime import RuntimePaths


def queue_verified(store: JobStore, root: Path, log) -> None:
    state = ControlState.runtime()
    if state is None:
        return
    registry = load()
    active = {j["service_id"] for j in store.active_jobs()}
    for service_id in OIDC_CONTRACTS:
        saved = state.service_identity(service_id) or {}
        installation = state.installation(service_id) or {}
        if installation.get("state") != "running" or service_id in active:
            continue
        if saved.get("state", "unconfigured") not in {"unconfigured", "migration_required"}:
            continue
        try:
            owner = onboarding_state.read(service_id).get("owner")
            if not owner:
                continue
            project = RuntimePaths().projects / service_id
            if not linked_owner_verified(registry.get(service_id), project, owner, lambda _line: None):
                continue
            job = store.create(
                kind="lifecycle",
                service_id=service_id,
                action="configure_identity",
                actor="onboarding",
                detail="Automatically completing verified Authentik sign-in.",
            )
            state.set_service_identity(
                service_id,
                "native_oidc",
                "configuring",
                owner_uid=owner["owner_uid"],
                job_id=job["id"],
                detail="Completing single sign-on automatically.",
            )
        except (OSError, ValueError) as exc:
            log(f"{service_id} sign-in verification deferred: {redact(str(exc))}")
