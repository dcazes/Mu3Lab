"""Browser projection of every curated service's live and persisted state."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ctl import __version__, service_config
from ctl.api.security import IdentityData
from ctl.control_state import ControlState
from ctl.identity import projection as identity_projection
from ctl.jobs import JobStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions, project_path
from ctl.service_state import compose_snapshot, tailnet_dns_name, tailnet_serve_ports
from ctl.service_state import status as service_status

ROOT = Path(__file__).resolve().parents[2]
INSTALLED_STATES = frozenset({"ready", "running", "starting", "stopped", "needs_setup"})
ACTIVE_WORKFLOW_STATES = frozenset({"queued", "installing", "starting", "verifying", "config_required"})


def service_snapshot(identity: IdentityData | None = None) -> dict[str, Any]:
    """Read the curated catalog and project live, non-mutating service health."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "services": []}
    dns_name = tailnet_dns_name()
    route_ports = tailnet_serve_ports()
    project_states, container_snapshots = compose_snapshot()
    store = JobStore.runtime()
    jobs = store.jobs(limit=100) if store else []
    control_state = ControlState.runtime()
    operator = bool(identity and identity["writes_enabled"])
    with ThreadPoolExecutor(max_workers=min(4, len(registry.services))) as pool:
        statuses = list(
            pool.map(
                lambda service: service_status(service, dns_name, ROOT, route_ports, project_states), registry.services
            )
        )
    result = []
    for service, item in zip(registry.services, statuses, strict=True):
        live_state = str(item["state"])
        # Traversing the protected dashboard through Authentik is stronger
        # evidence than a static manifest route flag.
        if service.id == "authentik" and operator and item["health_state"] == "healthy":
            auth_url = f"https://{dns_name}" if dns_name else ""
            item.update(
                {
                    "state": "ready",
                    "lifecycle_state": "ready",
                    "setup_state": "configured",
                    "route_state": "verified",
                    "route_ready": True,
                    "url": auth_url,
                    "user_action": "Open securely",
                    "ui": {
                        "state": "ready",
                        "url": auth_url or None,
                        "label": "Open securely",
                        "authentication": "local",
                        "reason": "",
                    },
                }
            )
        latest = next((job for job in jobs if job["service_id"] == service.id), None)
        installation = control_state.installation(service.id) if control_state else None
        initialization = control_state.initialization(service.id) if control_state else None
        missing_config = service_config.missing_required(service)
        if missing_config and service.stage == "optional" and not installation:
            item["state"] = "config_required"
            item["lifecycle_state"] = "config_required"
            item["detail"] = "Complete the required app configuration before installation."
            item["missing_configuration"] = missing_config
        if installation and service.stage == "optional":
            persisted = str(installation["state"])
            # A historical failure must never hide Docker's current healthy
            # state, or a healthy app would be offered a second installation.
            workflow_active = persisted in ACTIVE_WORKFLOW_STATES
            if workflow_active or (persisted == "failed" and live_state not in INSTALLED_STATES):
                item["state"] = persisted
                item["lifecycle_state"] = persisted
            item["installation"] = installation
            if item["state"] not in INSTALLED_STATES:
                item["route_state"] = installation["route_state"]
            item["last_error"] = installation.get("last_error", {})
        item["initialization"] = initialization or {
            "mode": service.account.get("mode", "none"),
            "state": "pending" if service.account.get("mode", "none") != "none" else "not_required",
        }
        compose_dir = project_path(service, ROOT)
        item["containers"] = container_snapshots.get(str(compose_dir.resolve()), [])
        item["last_job"] = latest
        item["last_job_id"] = str(latest["id"]) if latest else ""
        if service.stage == "optional":
            if item["state"] in INSTALLED_STATES:
                item["installation_state"] = "installed"
            elif item["compose_present"]:
                item["installation_state"] = "partial" if installation else "restore_available"
            elif installation and str(installation["state"]) == "failed":
                item["installation_state"] = "failed_setup"
            else:
                item["installation_state"] = "not_installed"
        else:
            item["installation_state"] = (
                "installed" if item["state"] in {"ready", "running", "starting", "stopped"} else "not_installed"
            )
        item["operational_state"] = live_state
        if item["installation_state"] == "restore_available":
            item["recommended_action"] = "restore"
        elif item["state"] in {"failed", "needs_attention", "degraded"}:
            item["recommended_action"] = "view_logs"
        elif item["state"] in {"planned", "not_installed"}:
            item["recommended_action"] = "install"
        else:
            item["recommended_action"] = "none"
        item["allowed_actions"] = allowed_actions(service, str(item["state"])) if operator else []
        item["identity"] = identity_projection(service, item, control_state)
        result.append(item)
    return {
        "ok": True,
        "version": __version__,
        "tailnet_dns_name": dns_name,
        "runtime": RuntimePaths().as_dict(),
        "services": result,
    }


def service_states(identity: IdentityData | None = None) -> dict[str, str]:
    return {item["id"]: item["state"] for item in service_snapshot(identity)["services"]}
