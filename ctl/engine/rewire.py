"""Re-render apps that work with another app when that app arrives or leaves.

An app's manifest can declare ``integrates_with`` a capability (Open WebUI
uses speech when a speech app is installed). After a provider is installed
or uninstalled, each installed consumer's project is rendered again, its
rules refresh anything they generate, and its containers restart only if a
generated file actually changed. A consumer's failure is logged and never
undoes the provider's own install or removal.

Each consumer is re-wired under its own app lock, so it never changes while
another job works on it, and a consumer waiting for recovery after a failed
update or restore is left alone until a person settles it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from ctl import resource_locks
from ctl.compute import resolved_mode
from ctl.control_state import ControlState
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StepFailed
from ctl.engine.project import Facts, installed
from ctl.engine.runtime import render_rules
from ctl.jobs import redact
from ctl.manifest.catalog import App, Catalog
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name
from ctl.store import onboarding as onboarding_state

Log = Callable[[str], None]
# How long re-wiring waits for a consumer that another job holds.
LOCK_WAIT_SECONDS = 600


def consumers(provider: App, catalog: Catalog, paths: RuntimePaths) -> list[App]:
    """Installed apps whose settings depend on one of ``provider``'s capabilities."""
    offered = set(provider.manifest.capabilities)
    return [
        app
        for app in catalog.apps
        if app.id != provider.id
        and installed(app, paths)
        and any(item.capability in offered for item in app.manifest.integrates_with)
    ]


def _fingerprint(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(project)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _running(app: App, paths: RuntimePaths) -> bool:
    state = ControlState.runtime(paths)
    installation = state.installation(app.id) if state else None
    return bool(installation and installation["state"] in {"running", "degraded"})


def rewire_one(app: App, facts: Facts, log: Log) -> bool:
    """Render one consumer again; restart it if it is running and anything changed. True when restarted."""
    project = facts.paths.projects / app.id
    before = _fingerprint(project)
    rules = rules_for(app.manifest)
    owner = onboarding_state.read(app.id, facts.paths).get("owner")
    render_rules(app, facts, rules, owner, copy_files=False)
    compose = Compose(project, gpu_mode=resolved_mode() if app.manifest.service.uses_gpu else "cpu")
    ctx = HookContext(app, project, compose, log, lambda name, detail: log(f"{name}: {detail}"), owner, facts)
    for rule in rules:
        rule.rewire(ctx)
    if _fingerprint(project) == before or not _running(app, facts.paths):
        return False
    rc, output = compose.up(log, wait_seconds=app.manifest.service.start_timeout_seconds, recreate=True)
    if rc:
        raise StepFailed("rewire", "rewire_restart_failed", output or f"{app.manifest.name} did not restart.")
    return True


def rewire_consumers(provider: App, catalog: Catalog, log: Log, paths: RuntimePaths | None = None) -> list[str]:
    """Update every installed app that works with ``provider``; return the names that restarted."""
    paths = paths or RuntimePaths()
    facts = Facts(tailnet_dns_name(), paths, catalog)
    restarted: list[str] = []
    from ctl.operations import OperationStore

    journal = OperationStore.runtime(paths)
    for app in consumers(provider, catalog, paths):
        if journal and journal.needing_attention(app.id):
            log(f"{app.manifest.name} needs attention first, so it was not updated for {provider.manifest.name}.")
            continue
        try:
            with resource_locks.hold(f"app:{app.id}", timeout=LOCK_WAIT_SECONDS, paths=paths):
                if rewire_one(app, facts, log):
                    restarted.append(app.manifest.name)
                    log(f"{app.manifest.name} now reflects {provider.manifest.name}.")
        except resource_locks.Busy:
            log(f"{app.manifest.name} is busy with other work, so it was not updated for {provider.manifest.name}.")
        except (StepFailed, OSError, ValueError, RuntimeError) as exc:
            log(f"{app.manifest.name} could not be updated for {provider.manifest.name}: {redact(str(exc))}")
    return restarted
