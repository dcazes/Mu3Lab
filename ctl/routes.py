"""Deterministic private route generation for curated optional services."""

from __future__ import annotations

import re
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.engine.launch import caddy_handler
from ctl.registry import Registry, Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name

START = "# BEGIN MU3LAB GENERATED APP ROUTES"
END = "# END MU3LAB GENERATED APP ROUTES"


def _generated_services(registry: Registry, root: Path, exclude: frozenset[str] = frozenset()) -> list[Service]:
    """Return installed generated routes, including installer-managed core apps."""
    state = ControlState.runtime()
    enabled: list[Service] = []
    for service in registry.services:
        if (
            service.manifest.route is None
            or not service.manifest.route.generated
            or service.proxy_port is None
            or service.id in exclude
        ):
            continue
        installed = state.installation(service.id) if state else None
        live = service_status(service, tailnet_dns_name(), root)
        if live["state"] in {"ready", "running", "starting", "stopped", "needs_setup"} or (
            installed and installed["state"] in {"running", "stopped", "degraded"}
        ):
            enabled.append(service)
    return enabled


def _write_candidate(registry: Registry, root: Path, exclude: frozenset[str] = frozenset()) -> tuple[Path, Path, str]:
    """Render from the checked-in base so runtime route files cannot go stale."""
    paths = RuntimePaths()
    target = paths.projects / "ingress" / "Caddyfile"
    source = root / "apps" / "ingress" / "Caddyfile.authenticated"
    if not source.is_file():
        raise OSError("Authenticated Caddy base configuration is missing.")
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    candidate = target.with_suffix(".candidate")
    base = source.read_text(encoding="utf-8")
    # Task D moves legacy core blocks out of the base. Until then, avoid
    # defining the same listener twice while generating all other core routes.
    base_ports = {int(port) for port in re.findall(r"^:(\d+)\s*\{", base, re.MULTILINE)}
    services = [item for item in _generated_services(registry, root, exclude) if item.proxy_port not in base_ports]
    candidate.write_text(render(base, services), encoding="utf-8")
    ingress_token = read_runtime_env(root / ".env").get("MU3LAB_INGRESS_TOKEN", "")
    return target, candidate, ingress_token


def _activate_candidate(root: Path, target: Path, candidate: Path, ingress_token: str, log) -> tuple[bool, str]:
    """Validate and activate the rendered Caddy configuration."""
    if not ingress_token:
        candidate.unlink(missing_ok=True)
        return False, "Private ingress token is missing."
    ingress = root / "apps" / "ingress"
    rc, output = actions.compose_up(
        ingress,
        log,
        env={"MU3LAB_CADDYFILE": str(candidate), "MU3LAB_INGRESS_TOKEN": ingress_token},
        recreate=True,
    )
    if rc:
        candidate.unlink(missing_ok=True)
        return False, output or "Caddy rejected the generated route."
    candidate.replace(target)
    rc, output = actions.compose_up(
        ingress,
        log,
        env={"MU3LAB_CADDYFILE": str(target), "MU3LAB_INGRESS_TOKEN": ingress_token},
        recreate=True,
    )
    if rc:
        return False, output or "Caddy could not activate the stable route file."
    return True, "Caddy routes activated."


def _block(service: Service) -> str:
    assert service.proxy_port is not None
    if service.id == "surfsense":
        return f"""
:{service.proxy_port} {{
\tbind 127.0.0.1
\troute {{
\t\treverse_proxy /outpost.goauthentik.io/* 127.0.0.1:9001
\t\tforward_auth 127.0.0.1:9001 {{
\t\t\turi /outpost.goauthentik.io/auth/caddy
\t\t\theader_up Host {{http.request.hostport}}
\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t\theader_up X-Forwarded-Proto https
\t\t\tcopy_headers X-Authentik-Username X-Authentik-Email X-Authentik-Name X-Authentik-Groups
\t\t\ttrusted_proxies private_ranges
\t\t}}
\t\trespond /auth/register* "Accounts are provisioned by Mu3Lab." 403
\t\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t}}
\t}}
}}
""".strip()
    if service.id == "baby-buddy":
        # The phone apps call Baby Buddy's API with a per-person API token and
        # cannot complete a browser sign-in. Requests that carry one skip the
        # Authentik gate and Baby Buddy checks the token itself; Remote-User is
        # always stripped there, so the bypass can never claim someone's identity.
        return f"""
:{service.proxy_port} {{
\tbind 127.0.0.1
\t@api_token {{
\t\tpath /api/*
\t\theader Authorization "Token *"
\t}}
\troute {{
\t\thandle @api_token {{
\t\t\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\t\t\theader_up -Remote-User
\t\t\t\theader_up X-Forwarded-Proto https
\t\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t\t}}
\t\t}}
\t\treverse_proxy /outpost.goauthentik.io/* 127.0.0.1:9001
\t\tforward_auth 127.0.0.1:9001 {{
\t\t\turi /outpost.goauthentik.io/auth/caddy
\t\t\theader_up Host {{http.request.hostport}}
\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t\theader_up X-Forwarded-Proto https
\t\t\tcopy_headers X-Authentik-Username
\t\t\ttrusted_proxies private_ranges
\t\t}}
\t\t# Set replaces any client-sent Remote-User. Never also delete
\t\t# Remote-User in this block: Caddy applies deletes after sets, so the
\t\t# verified identity would be dropped and Baby Buddy shows its login.
\t\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\t\theader_up Remote-User {{http.request.header.X-Authentik-Username}}
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t}}
\t}}
}}
""".strip()
    return f"""
:{service.proxy_port} {{
\tbind 127.0.0.1
{caddy_handler(service.manifest)}\thandle {{
\t\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {{http.request.hostport}}
\t\t}}
\t}}
}}
""".strip()


def render(base: str, services: list[Service]) -> str:
    """Replace the owned generated section; preserve hand-reviewed base routes."""
    if START in base:
        before = base.split(START, 1)[0].rstrip()
        after_section = base.split(START, 1)[1]
        after = after_section.split(END, 1)[1].lstrip() if END in after_section else ""
    else:
        before, after = base.rstrip(), ""
    body = "\n\n".join(_block(service) for service in services)
    pieces = [before, START, body, END]
    if after:
        pieces.append(after.rstrip())
    return "\n\n".join(piece for piece in pieces if piece != "") + "\n"


def base_matches(base: str, deployed: str) -> bool:
    """Whether the deployed Caddyfile was rendered from this checked-in base."""
    return deployed.split(START, 1)[0].strip() == base.split(START, 1)[0].strip()


def rebase(base: str, deployed: str) -> str:
    """Render a new checked-in base while keeping the deployed app routes."""
    if START not in deployed:
        return render(base, [])
    section = deployed.split(START, 1)[1]
    body = section.split(END, 1)[0].strip() if END in section else ""
    before = base.split(START, 1)[0].rstrip()
    return "\n\n".join(piece for piece in (before, START, body, END) if piece) + "\n"


def apply(registry: Registry, current: Service, root: Path, log) -> tuple[bool, str]:
    """Render Caddy and publish one route, using registry-owned port values."""
    if current.private_https_port is None or current.proxy_port is None:
        return True, "Service is internal-only; no private route is required."
    try:
        target, temporary, ingress_token = _write_candidate(registry, root)
    except OSError as exc:
        return False, str(exc)
    activated, detail = _activate_candidate(root, target, temporary, ingress_token, log)
    if not activated:
        return False, detail
    published = actions.tailscale_serve(current.private_https_port, current.proxy_port, log)
    if not published.get("ok"):
        terminal_command = published.get("terminal_command")
        if terminal_command:
            return False, (
                "Administrator action required before this route can be published. "
                f"Run `{terminal_command}` once, then retry the installation."
            )
        return False, str(published.get("log", ["Tailscale Serve failed."])[-1])
    return True, "Private HTTPS route published."


def withdraw(registry: Registry, current: Service, root: Path, log) -> tuple[bool, str]:
    """Remove one uninstalled app's Caddy route and Tailscale HTTPS port."""
    if current.private_https_port is None or current.proxy_port is None:
        return True, "Service is internal-only; no private route to remove."
    try:
        target, temporary, ingress_token = _write_candidate(registry, root, frozenset({current.id}))
    except OSError as exc:
        return False, str(exc)
    activated, detail = _activate_candidate(root, target, temporary, ingress_token, log)
    if not activated:
        return False, detail
    removed = actions.tailscale_serve_off(current.private_https_port, log)
    if not removed.get("ok"):
        terminal_command = removed.get("terminal_command")
        if terminal_command:
            return False, (
                "Administrator action required before this route can be removed. "
                f"Run `{terminal_command}` once, then retry the uninstall."
            )
        return False, str(removed.get("log", ["Tailscale Serve failed."])[-1])
    return True, "Private HTTPS route removed."


def reconcile_core(registry: Registry, root: Path, log) -> tuple[bool, str]:
    """Repair core UI routes without reinstalling or recreating core apps."""
    try:
        target, temporary, ingress_token = _write_candidate(registry, root)
    except OSError as exc:
        return False, str(exc)
    activated, detail = _activate_candidate(root, target, temporary, ingress_token, log)
    if not activated:
        return False, detail
    for service_id in ("lobehub", "litellm", "freellmapi"):
        service = registry.get(service_id)
        if service.private_https_port is None or service.proxy_port is None:
            continue
        published = actions.tailscale_serve(service.private_https_port, service.proxy_port, log)
        if not published.get("ok"):
            terminal_command = published.get("terminal_command")
            if terminal_command:
                return (
                    False,
                    f"Administrator action required before this route can be published. Run `{terminal_command}` once, then retry.",
                )
            return False, str(published.get("log", ["Tailscale Serve failed."])[-1])
    return True, "Core UI routes and Tailscale HTTPS endpoints are active."
