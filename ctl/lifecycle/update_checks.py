"""What an update must not break: sign-in, the app's chat connections and its Home widgets.

A new release can start and answer its health check yet no longer let people sign in, or refuse
the calls its chat connector or widgets make (Paperless 3.5 began turning away every address but
its public one). So before an update stops the app, each of these is tried once, and afterwards
every one that worked is tried again, patiently, while the new release settles. One that worked
before and fails after is a regression, and the update puts the previous release and its data
back. Checks that did not work before the update (a widget nobody set up, a connection switched
off) are not held against the new release.

Each check is read-only, as the owner whose key the app's own connector holds: sign-in is walked
up to where Authentik would ask who you are, a connector lists its tools and calls one that only
reads, and each widget reads its figures.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctl.jobs import redact
from ctl.registry import Service

Log = Callable[[str], None]
# One try: True passed, False failed, None does not apply here (nothing to compare).
Probe = Callable[[float], bool | None]
# How long the new release gets to make every check pass again.
SETTLE_SECONDS = 300
RETRY_SECONDS = 10


def _sign_in(service: Service) -> Probe | None:
    if service.manifest.sign_in.method == "none" or service.private_https_port is None:
        return None

    def probe(patience: float) -> bool | None:
        from ctl.lifecycle.signin_check import verify_sign_in
        from ctl.service_state import tailnet_dns_name

        host = tailnet_dns_name()
        if not host:
            return None
        try:
            verify_sign_in(service.id, host, lambda _line: None, patience=patience)
        except Exception:  # SignInError, or the app or Authentik not answering
            return False
        return True

    return probe


def _read_tool(tools: list[dict[str, Any]]) -> str:
    """A tool that only reads and needs no arguments, preferring lists."""
    plain = [
        tool
        for tool in tools
        if tool.get("risk") == "read" and not ((tool.get("parameters") or {}).get("required") or [])
    ]
    plain.sort(key=lambda tool: (not str(tool["id"]).startswith("list"), str(tool["id"])))
    return str(plain[0]["id"]) if plain else ""


def _connector(server: Any) -> Probe:
    def probe(_patience: float) -> bool | None:
        from ctl import mcp_ops
        from ctl.control_state import ControlState
        from ctl.mcp_registry import credential_path
        from ctl.secrets import read_runtime_env

        state = ControlState.runtime()
        runtime = state.mcp_server(server.id) if state is not None else None
        if not runtime or not runtime.get("enabled"):
            return None
        values = read_runtime_env(credential_path(server.id))
        try:
            tools = mcp_ops._discover_tools(server, values)
            name = _read_tool(tools) if server.transport != "openapi-bridge" else ""
            if not name:
                return True
            url = mcp_ops._local_endpoint(server)
            token = values.get("MCP_AUTH_TOKEN", "")
            extra = getattr(server, "headers", ())
            _, session = mcp_ops._rpc_request(url, "initialize", 1, token=token, extra=extra)
            mcp_ops._rpc_notification(url, "notifications/initialized", token=token, session_id=session, extra=extra)
            reply, _ = mcp_ops._rpc_request(
                url,
                "tools/call",
                2,
                token=token,
                session_id=session,
                params={"name": name, "arguments": {}},
                extra=extra,
            )
        except Exception:
            return False
        result = reply.get("result")
        return isinstance(result, dict) and not result.get("isError")

    return probe


def _widget(app: Any, spec: Any) -> Probe:
    def probe(_patience: float) -> bool | None:
        from ctl import app_widgets, person_connectors
        from ctl.runtime import RuntimePaths

        context = app_widgets.WidgetContext(
            app, True, RuntimePaths(), uid="", email=person_connectors.owner_email(app.id), personal=spec.personal
        )
        try:
            app_widgets._reader(app, spec)(context)
        except app_widgets.NeedsSetup:
            return None
        except Exception:
            return False
        return True

    return probe


def probes(service: Service, root: Path) -> dict[str, Probe]:
    """Every check that applies to ``service``, by a name people can read."""
    found: dict[str, Probe] = {}
    sign_in = _sign_in(service)
    if sign_in:
        found["sign-in"] = sign_in
    try:
        from ctl.mcp_catalog import for_service
        from ctl.mcp_catalog import load as load_connectors
        from ctl.registry import load as load_registry

        for server in for_service(load_connectors(load_registry()), service.id):
            found[f"chat connection {server.name}"] = _connector(server)
    except Exception:  # an unreadable connector catalog leaves the other checks
        pass
    try:
        from ctl.manifest.catalog import load as load_catalog

        app = load_catalog(root / "apps").get(service.id)
        for spec in app.manifest.widgets:
            found[f"widget {spec.title}"] = _widget(app, spec)
    except Exception:
        pass
    return found


def baseline(service: Service, root: Path, log: Log) -> list[str]:
    """The checks that pass now, before the update; each is tried once."""
    passed = []
    for name, probe in probes(service, root).items():
        try:
            if probe(0) is True:
                passed.append(name)
        except Exception as exc:  # a broken check never stops an update
            log(f"Mu3Lab could not try {name} before updating: {redact(str(exc))}")
    if passed:
        log(f"Working before the update, and checked again after it: {', '.join(passed)}.")
    return passed


def regressions(
    service: Service, root: Path, passed: list[str], log: Log, *, settle: float = SETTLE_SECONDS, sleep=time.sleep
) -> list[str]:
    """The checks from ``baseline`` that still fail once the new release had ``settle`` seconds."""
    if not passed:
        return []
    available = probes(service, root)
    waiting = [name for name in passed if name in available]
    deadline = time.monotonic() + settle
    while True:
        waiting = [name for name in waiting if _still_failing(available[name], deadline)]
        if not waiting or time.monotonic() >= deadline:
            break
        sleep(RETRY_SECONDS)
    if waiting:
        log(f"Stopped working after the update: {', '.join(waiting)}.")
    else:
        log(f"Still working after the update: {', '.join(passed)}.")
    return waiting


def _still_failing(probe: Probe, deadline: float) -> bool:
    """Whether ``probe`` fails again (so it is tried again until the deadline)."""
    try:
        outcome = probe(max(0.0, min(60.0, deadline - time.monotonic())))
    except Exception:
        return True
    return outcome is False
