"""Browser projection of every curated service's live and persisted state."""

from __future__ import annotations

import time
from pathlib import Path

from ctl import __version__, service_config
from ctl.api import models
from ctl.api.security import IdentityData
from ctl.control_state import ControlState
from ctl.identity import projection as identity_projection
from ctl.jobs import JobStore
from ctl.registry import RegistryError, Service
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions
from ctl.status import snapshots
from ctl.status.checks import OBSERVATION_MAX_AGE_SECONDS, Check, epoch, sign_in_check, stale
from ctl.status.display import projection as display_projection
from ctl.store import db, records

ROOT = Path(__file__).resolve().parents[2]
INSTALLED_STATES = frozenset({"ready", "running", "starting", "stopped", "needs_setup", "checking", "stale"})
USABLE_STATES = frozenset({"ready", "running", "needs_setup"})
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
    now = time.time()
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
        _apply_checks(service, item, control_state, now)
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
        if item.get("blocking_check") == "sign_in":
            # The identity row may still say ready; the evidence behind it has expired or failed.
            sign_in = item["checks"]["sign_in"]
            item["identity"] |= {
                "state": "configuring" if sign_in["state"] == "pending" else "degraded",
                "detail": sign_in["detail"],
            }
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


def _legacy_checks(service: Service, item: dict) -> tuple[Check, Check]:
    """Checks for an observation written before typed checks existed."""
    process = Check("pass") if item.get("health_state") == "healthy" else Check("pending")
    route_state = item.get("route_state")
    if service.private_https_port is None or route_state == "not_required":
        route = Check("not_required")
    elif route_state == "verified":
        route = Check("pass")
    else:
        route = Check("fail", "The app's required private HTTPS route has not passed verification.")
    return process, route


def _apply_checks(service: Service, item: dict, control_state: ControlState | None, now: float) -> None:
    """Allow a usable claim and launch link only while every required check holds.

    Observations age between worker cycles and sign-in evidence ages between
    its periodic checks, so both are judged here, at read time.
    """
    stored = item.get("checks")
    raw: dict = stored if isinstance(stored, dict) else {}
    legacy_process, legacy_route = _legacy_checks(service, item)
    process = Check.load(raw.get("process")) or legacy_process
    route = Check.load(raw.get("route")) or legacy_route
    saved_identity = control_state.service_identity(service.id) if control_state else None
    sign_in = sign_in_check(service, saved_identity, now)
    observed_at = str(item.get("observed_at") or "")
    observed = epoch(observed_at)
    outdated = bool(observed_at) and (observed is None or now - observed > OBSERVATION_MAX_AGE_SECONDS)
    if outdated:
        process, route = stale(process, observed_at), stale(route, observed_at)
    item["checks"] = {"process": process.public(), "route": route.public(), "sign_in": sign_in.public()}
    state = str(item.get("state") or "unknown")
    if outdated and state not in {"planned", "unknown"}:
        item["state"] = item["lifecycle_state"] = "stale"
        item["detail"] = "Mu3Lab has not refreshed this app's status recently; the background worker may be stopped."
        item["blocking_check"] = "process"
        _suppress_launch(service, item)
        return
    if state not in USABLE_STATES:
        return
    blocking = next(
        ((name, check) for name, check in (("route", route), ("sign_in", sign_in)) if not check.satisfied), None
    )
    if blocking is None:
        item["blocking_check"] = None
        item["state"] = item["lifecycle_state"] = "checking" if route.state == "checking" else "ready"
        if route.state == "checking":
            item["detail"] = route.detail
        return
    name, check = blocking
    item["blocking_check"] = name
    item["state"] = item["lifecycle_state"] = "checking" if check.state == "pending" else "needs_setup"
    item["detail"] = check.detail
    _suppress_launch(service, item, route_failed=name == "route")


def _suppress_launch(service: Service, item: dict, *, route_failed: bool = True) -> None:
    if route_failed:
        item["route_ready"] = False
    item["url"] = ""
    item["ui"] = dict(item.get("ui") or {}) | {
        "state": "route_pending" if service.ui.get("available") else "unavailable",
        "url": None,
    }


def service_states(identity: IdentityData | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in service_snapshot(identity).services:
        display = item.display_state
        process = (item.checks.process.state if item.checks else "") == "pass"
        result[item.id] = (
            "needs_setup"
            if display == "needs_attention" and item.installed
            else "ready"
            if display == "running" or (display == "checking" and process)
            else "starting"
            if display in {"working", "checking"}
            else display
        )
    return result
