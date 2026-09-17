"""Curated core-suite plan and executor.

The executor accepts no user-supplied Compose path or command. It walks the
fixed dependency order from the registry, stops at the first failure, and
stores only redacted job metadata. Service-specific configuration and secret
submission are deliberately separate follow-up flows.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ctl import actions
from ctl.jobs import JobStore, redact
from ctl.registry import RegistryError, load
from ctl.runtime import RuntimePaths
from ctl.secrets import ensure_core_envs
from ctl.service_state import status as service_status

CORE_ORDER = ("ollama", "freellmapi", "litellm", "open-webui", "firecrawl", "surfsense")
HEALTH_TIMEOUT_SECONDS = 120


def plan(root: Path) -> dict:
    """Return a safe, deterministic plan without mutating the host."""
    try:
        registry = load()
    except RegistryError as exc:
        return {"ready": False, "error": str(exc), "services": [], "missing": list(CORE_ORDER)}
    known = {service.id: service for service in registry.services}
    missing = [service_id for service_id in CORE_ORDER
               if service_id not in known or not (known[service_id].compose_path(root) / "docker-compose.yml").is_file()]
    return {"ready": not missing, "error": "" if not missing else "Core manifests are not ready for execution.",
            "services": list(CORE_ORDER), "missing": missing}


def _run(store: JobStore, job_id: str, actor: str, root: Path,
         logger: Callable[[str], None] | None = None) -> None:
    """Run the reviewed sequence in a detached worker."""
    log = logger or (lambda _line: None)
    try:
        store.transition(job_id, "running", actor=actor, detail="Core suite execution started.")
        checked = plan(root)
        if not checked["ready"]:
            store.transition(job_id, "failed", actor=actor,
                             detail="Core suite blocked: " + ", ".join(checked["missing"]))
            return
        runtime = RuntimePaths()
        env_files = ensure_core_envs(runtime.root)
        for service_id in CORE_ORDER:
            service = load().get(service_id)
            project = service.compose_path(root)
            rc, output = actions.compose_up(
                project, log,
                env={"MU3LAB_ENV_FILE": str(env_files[service_id]),
                     "MU3LAB_DATA_ROOT": str(runtime.data)})
            safe_output = redact(output or f"exit {rc}")
            if rc != 0:
                store.transition(job_id, "failed", actor=actor,
                                 detail=f"Stopped at {service_id}: {safe_output}")
                return
            deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                live = service_status(service, "", root)
                if live["health_state"] == "healthy":
                    break
                time.sleep(2)
            else:
                live = service_status(service, "", root)
                store.transition(job_id, "failed", actor=actor,
                                 detail=f"Stopped at {service_id}: health check did not pass ({redact(live['detail'])}).")
                return
            log(f"{service_id}: compose start completed")
        store.transition(job_id, "succeeded", actor=actor,
                         detail="Core suite started and all declared health checks passed; provider and model wiring can now be configured.")
    except Exception as exc:  # noqa: BLE001 - job state must become terminal
        store.transition(job_id, "failed", actor=actor,
                         detail=f"Core executor failed safely: {redact(str(exc))}")


def start(store: JobStore, actor: str, root: Path) -> dict[str, str]:
    """Create and asynchronously execute one core-suite job."""
    job = store.create(kind="lifecycle", service_id="core-suite", action="install",
                       actor=actor, detail="Install the reviewed imperative suite")
    thread = threading.Thread(target=_run, args=(store, job["id"], actor, root), daemon=True)
    thread.start()
    return job
