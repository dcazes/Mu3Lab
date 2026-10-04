"""Render runtime projects with their manifest's environment rules."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from ctl.engine.project import Facts, render
from ctl.manifest.catalog import App
from ctl.registry import Service, load
from ctl.rules import Rule, rules_for
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name
from ctl.store import onboarding as onboarding_state
from ctl.store.workflows import JobIdentity


def render_rules(app: App, facts: Facts, rules: list[Rule], owner: JobIdentity | None) -> Path:
    return render(app, facts, hooks=[partial(rule.prepare_env, owner=owner) for rule in rules])


def render_service(service: Service, root: Path) -> Path:
    catalog = load().catalog
    app = catalog.get(service.id)
    paths = RuntimePaths()
    return render_rules(
        app,
        Facts(tailnet_dns_name(), paths, catalog),
        rules_for(app.manifest),
        onboarding_state.read(service.id, paths).get("owner"),
    )
