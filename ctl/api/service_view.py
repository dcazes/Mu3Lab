"""Browser projection of every curated service's live and persisted state."""

from __future__ import annotations

from pathlib import Path

from ctl import __version__, service_config
from ctl.api import models
from ctl.api.security import IdentityData
from ctl.control_state import ControlState
from ctl.identity import projection as identity_projection
from ctl.jobs import JobStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions
from ctl.status import snapshots
from ctl.status.display import projection as display_projection
from ctl.store import db, records

ROOT = Path(__file__).resolve().parents[2]
INSTALLED_STATES = frozenset({"ready", "running", "starting", "stopped", "needs_setup"})
ACTIVE_WORKFLOW_STATES = frozenset({"queued", "installing", "starting", "verifying", "uninstalling", "config_required"})


def service_snapshot(identity: IdentityData | None = None) -> models.ServicesResponse:
    path = db.database()
    if not path.is_file():
        return models.ServicesResponse.model_validate(_service_snapshot(identity))
    with db.connect(path) as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN")
        return models.ServicesResponse.model_validate(_service_snapshot(identity))


def _service_snapshot(identity: IdentityData | None = None) -> object:
    """Read the curated catalog and project live, non-mutating service health."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "services": []}
    dns_name = str(records.get("status", "network").get("dns_name", ""))
    observed = snapshots.read()
    store = JobStore.runtime()
    # Active jobs first, however old, so a long-running one is never missed.
    jobs = list({job["id"]: job for job in [*store.active_jobs(), *store.jobs(limit=100)]}.values()) if store else []
    control_state = ControlState.runtime()
    operator = bool(identity and identity["writes_enabled"])
    statuses = [
        observed.get(service.id)
        or {
            **service.public(),
            "state": "unknown",
            "health_state": "unknown",
            "route_state": "unknown",
            "route_ready": False,
            "compose_present": False,
            "url": "",
            "last_error": "",
            "ui": {
                "state": "route_pending" if service.ui.get("available") else "unavailable",
                "url": None,
                "label": "Open",
                "authentication": service.auth,
                "reason": "Waiting for the worker to check this app.",
            },
            "update": None,
            "detail": "Waiting for the worker to check this app.",
            "observed_at": "",
            "containers": [],
        }
        for service in registry.services
    ]
    result = []
    for service, original in zip(registry.services, statuses, strict=True):
        item = dict(original)
        if service.private_https_port is not None and item.get("route_state") != "verified":
            item["route_ready"] = False
            item["url"] = ""
            item["ui"] = dict(item.get("ui") or {}) | {
                "state": "route_pending" if service.ui.get("available") else "unavailable",
                "url": None,
            }
            if item.get("state") in {"ready", "running"}:
                item["state"] = item["lifecycle_state"] = "needs_setup"
                item["detail"] = "The app's required private HTTPS route has not passed verification."
        live_state = str(item["state"])
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
            item["installation_state"] = "installed" if item["state"] in INSTALLED_STATES else "not_installed"
        item["operational_state"] = live_state
        if item["installation_state"] == "restore_available":
            item["recommended_action"] = "restore"
        elif item["state"] in {"failed", "needs_attention", "degraded"}:
            item["recommended_action"] = "view_logs"
        elif item["state"] in {"planned", "not_installed"}:
            item["recommended_action"] = "install"
        else:
            item["recommended_action"] = "none"
        allowed = allowed_actions(service, str(item["state"])) if operator else []
        if operator and not (identity and identity["is_admin"]):
            # Household members may add apps; running and removing them is administration.
            allowed = [action for action in allowed if action in {"install", "retry_setup"}]
        item["allowed_actions"] = allowed
        item["identity"] = identity_projection(service, item, control_state)
        if not item.get("mobile"):
            item.pop("mobile", None)
        if latest and latest["state"] in {"queued", "running"}:
            item["state"] = "queued" if latest["state"] == "queued" else "verifying"
            item["detail"] = str(latest.get("detail") or "An operation is in progress.")
        item.update(display_projection(item))
        for legacy in (
            "state",
            "installation_state",
            "operational_state",
            "lifecycle_state",
            "setup_state",
            "route_state",
        ):
            item.pop(legacy, None)
        result.append(item)
    return {
        "ok": True,
        "version": __version__,
        "tailnet_dns_name": dns_name,
        "runtime": RuntimePaths().as_dict(),
        "observed_at": min((str(item.get("observed_at") or "") for item in result), default=""),
        "services": result,
    }


def service_states(identity: IdentityData | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in service_snapshot(identity).services:
        display = item.display_state
        result[item.id] = (
            "needs_setup"
            if display == "needs_attention" and item.installed
            else "ready"
            if display == "running"
            else "starting"
            if display == "working"
            else display
        )
    return result
