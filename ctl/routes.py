"""Deterministic private route generation for curated optional services."""

from __future__ import annotations

from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.registry import Registry, Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

START = "# BEGIN MU3LAB GENERATED APP ROUTES"
END = "# END MU3LAB GENERATED APP ROUTES"


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
\t\t\theader_up Host {{http.request.host}}
\t\t\theader_up X-Forwarded-Host {{http.request.host}}
\t\t\theader_up X-Forwarded-Proto https
\t\t\tcopy_headers X-Authentik-Username X-Authentik-Email X-Authentik-Name X-Authentik-Groups
\t\t\ttrusted_proxies private_ranges
\t\t}}
\t\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {{http.request.host}}
\t\t}}
\t}}
}}
""".strip()
    return f"""
:{service.proxy_port} {{
\tbind 127.0.0.1
\treverse_proxy 127.0.0.1:{service.https_port} {{
\t\theader_up X-Forwarded-Proto https
\t\theader_up X-Forwarded-Host {{http.request.host}}
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


def apply(registry: Registry, current: Service, root: Path,
          log) -> tuple[bool, str]:
    """Render Caddy and publish one route, using registry-owned port values."""
    if current.private_https_port is None or current.proxy_port is None:
        return True, "Service is internal-only; no private route is required."
    paths = RuntimePaths()
    target = paths.projects / "ingress" / "Caddyfile"
    source = root / "core" / "ingress" / "Caddyfile.authenticated"
    if not target.is_file():
        if not source.is_file():
            return False, "Authenticated Caddy base configuration is missing."
        target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    base = target.read_text(encoding="utf-8")
    state = ControlState.runtime()
    enabled: list[Service] = []
    for service in registry.services:
        if service.stage != "optional" or service.proxy_port is None:
            continue
        installed = state.installation(service.id) if state else None
        if service.id == current.id or (installed and installed["state"] in {"running", "stopped", "degraded"}):
            enabled.append(service)
    candidate = render(base, enabled)
    temporary = target.with_suffix(".candidate")
    temporary.write_text(candidate, encoding="utf-8")
    env = read_runtime_env(root / ".env")
    ingress_token = env.get("MU3LAB_INGRESS_TOKEN", "")
    if not ingress_token:
        temporary.unlink(missing_ok=True)
        return False, "Private ingress token is missing."
    # Caddy validates its mounted candidate as part of Compose start. Only
    # promote it after that succeeds, so a malformed route cannot replace the
    # last known-good runtime file.
    rc, output = actions.compose_up(
        root / "core" / "ingress", log,
        env={"MU3LAB_CADDYFILE": str(temporary), "MU3LAB_INGRESS_TOKEN": ingress_token},
        recreate=True,
    )
    if rc:
        temporary.unlink(missing_ok=True)
        return False, output or "Caddy rejected the generated route."
    temporary.replace(target)
    # Recreate once with the stable path, otherwise Docker retains a bind to
    # the temporary inode that was renamed above.
    rc, output = actions.compose_up(
        root / "core" / "ingress", log,
        env={"MU3LAB_CADDYFILE": str(target), "MU3LAB_INGRESS_TOKEN": ingress_token},
        recreate=True,
    )
    if rc:
        return False, output or "Caddy could not activate the stable route file."
    published = actions.tailscale_serve(current.private_https_port, current.proxy_port, log)
    if not published.get("ok"):
        return False, str(published.get("log", ["Tailscale Serve failed."])[-1])
    return True, "Private HTTPS route published."
