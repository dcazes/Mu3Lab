"""Shared test helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from ctl.core_setup import core_services
from ctl.engine.project import Facts
from ctl.engine.runtime import render_rules
from ctl.registry import load
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

_RUNTIME_PATH_USERS = (
    "ctl.service_ops",
    "ctl.engine.install",
    "ctl.engine.runtime",
    "ctl.lifecycle.uninstall",
    "ctl.lifecycle.app_releases",
    "ctl.identity",
    "ctl.install",
    "ctl.people",
)


@contextmanager
def runtime_paths(paths: RuntimePaths) -> Iterator[None]:
    """Point every application-lifecycle module at a temporary runtime root."""
    with ExitStack() as stack:
        for module in _RUNTIME_PATH_USERS:
            stack.enter_context(patch(f"{module}.RuntimePaths", return_value=paths))
        yield


def render_core_projects(paths: RuntimePaths) -> None:
    """Render core fixtures locally, supplying a fake already-minted service key."""
    registry = load()
    facts = Facts("test.tailnet.ts.net", paths, registry.catalog)
    for service in core_services(registry):
        app = registry.catalog.get(service.id)
        project = render_rules(app, facts, rules_for(app.manifest), None)
        if service.id == "freellmapi":
            env = read_runtime_env(project / ".env")
            env["FREELLMAPI_SERVICE_KEY"] = "sk-cp-test-key"
            env["MU3LAB_ADMIN_BOOTSTRAPPED"] = "true"
            (project / ".env").write_text(runtime_env_text(env))
