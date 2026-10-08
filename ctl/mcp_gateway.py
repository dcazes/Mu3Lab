"""The control plane's side of the tool gateway: policy, tokens and lifecycle.

Mu3Lab owns the gateway's policy.json: which connector serves each app, the
reviewed tools, and the owner's switches.  Every change here is written
atomically and the gateway re-reads it, so switches apply to the next call.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from ctl import actions, job_guard, people, platform_releases
from ctl.bootstrap import stamps
from ctl.mcp_activity import McpActivity
from ctl.mcp_registry import credential_path
from ctl.mcp_review import Review
from ctl.mcp_review import load as load_review
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked, write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store import records
from gateway_authority import Authority, canonical, schema_digest, validate_policy

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "platform" / "tool-gateway"
PORT = 8822
ENDPOINT = "http://mcp-gateway:8080/apps/{service_id}/mcp"
LOCAL_ENDPOINT = f"http://127.0.0.1:{PORT}" + "/apps/{service_id}/mcp"
META_TOOLS = ("find_tools", "use_tool", "change_with_tool")


def project() -> Path:
    return RuntimePaths().projects / "mcp-gateway"


def endpoint(service_id: str) -> str:
    return ENDPOINT.format(service_id=service_id)


def review_for(server) -> Review:
    return load_review(server.id, server.review, ROOT)


def authority() -> Authority:
    return Authority(project() / "authority")


def operator_token(subject: str, service_id: str) -> str:
    return authority().credential(subject, service_id)


def app_token(service_id: str) -> str:
    """Control-plane discovery only; never permits tool calls or human approval."""
    store = authority()
    store.grant_subject("control-discovery", operator=True)
    return store.credential("control-discovery", service_id, provider="control-discovery")


# --------------------------------------------------------------------------- switches


def tool_states(server, review: Review, activity: McpActivity | None = None) -> dict[str, Any]:
    """Resolve the owner's switches against the review's defaults.

    A category starts as its review says.  Inside it, reading tools start on
    and changing tools start off; an explicit choice always wins.
    """
    activity = activity or McpActivity()
    categories = activity.category_switches(server.id)
    choices = activity.explicit_permissions(server.id)
    return {
        "categories": {category.id: categories.get(category.id, category.default_on) for category in review.categories},
        "tools": {
            name: (choices[name] != "disabled") if name in choices else tool.access == "read"
            for name, tool in review.tools.items()
        },
    }


def instructions(app_name: str, review: Review, states: dict[str, Any]) -> str:
    """The category map every app assistant carries, including what is switched off."""
    hint = f"Mu3Lab: Settings, Chat integrations, {app_name}"
    lines = [
        f"You are the {app_name} assistant in Mu3Lab. You work only with {app_name}.",
        "",
        "The tools you can see directly are only the most common ones. Call find_tools to see more: with no",
        "arguments it lists every category below, and with a category it lists that category's tools and inputs.",
        "Run a reading tool with use_tool. Writes create a request for your approval in Mu3Lab.",
        "",
        f"{app_name} tool categories:",
    ]
    for category in review.categories:
        members = [tool for tool in review.tools.values() if tool.category == category.id]
        writes_off = any(tool.access == "write" and not states["tools"][tool.name] for tool in members)
        if not states["categories"][category.id]:
            state = "off"
        elif not any(states["tools"][tool.name] for tool in members):
            state = "off: its tools change data and are switched off" if writes_off else "off"
        elif writes_off:
            state = "on; tools that change data are off"
        else:
            state = "on"
        lines.append(f"- {category.title} ({state}): {category.summary}")
    lines += [
        "",
        "Rules:",
        "- Before saying something cannot be done, call find_tools to check.",
        f"- If the tool needed is switched off, say which category or tool the owner can switch on in {hint}.",
        "- A write switch only permits approval requests. A human must approve the exact arguments in Mu3Lab before each change.",
        "- Show approval_url to the operator when a change request returns one. Human approval happens only in Mu3Lab.",
        "- Never claim a change completed before its recorded outcome is succeeded.",
        "- These connectors expose shared service-account data to operators; they do not isolate personal records.",
        "- Use ids from earlier results; never guess them.",
    ]
    if review.guidance:
        lines += ["", review.guidance]
    return "\n".join(lines)


# --------------------------------------------------------------------------- policy


def app_policy(server, app_name: str) -> dict[str, Any]:
    review = review_for(server)
    states = tool_states(server, review)
    upstream_token = read_runtime_env(credential_path(server.id)).get("MCP_AUTH_TOKEN", "")
    from ctl.control_state import ControlState

    control = ControlState.runtime()
    runtime = control.mcp_server(server.id) if control else None
    schemas = {tool["id"]: tool.get("parameters") for tool in (runtime or {}).get("tool_snapshot", [])}
    dns = str(records.get("status", "network").get("dns_name", ""))
    route = by_capability("private_proxy").manifest.route
    approval_origin = f"https://{dns}:{route.https_port}" if dns and route else ""
    states["tools"] = {
        name: enabled
        and (review.tools[name].access == "read" or (isinstance(schemas.get(name), dict) and bool(approval_origin)))
        for name, enabled in states["tools"].items()
    }
    return {
        "name": app_name,
        "server_id": server.id,
        "data_scope": review.data_scope,
        "delegation": review.delegation,
        "audience": "operators",
        "approval_required": True,
        "approval_origin": approval_origin,
        "connector_revision": f"{server.revision}:{review.revision}",
        "upstream": {
            "url": server.endpoint,
            "headers": {"Authorization": f"Bearer {upstream_token}"} if upstream_token else {},
        },
        "instructions": instructions(app_name, review, states),
        "categories": [
            {
                "id": category.id,
                "title": category.title,
                "summary": category.summary,
                "enabled": states["categories"][category.id],
            }
            for category in review.categories
        ],
        "tools": {
            name: {
                "name": name,
                "category": tool.category,
                "access": tool.access,
                "core": tool.core,
                "enabled": states["tools"][name] and (tool.access == "read" or isinstance(schemas.get(name), dict)),
                "schema": schemas.get(name) or {"type": "object", "properties": {}},
                # Pins the schema Mu3Lab verified; the gateway withholds the tool if the connector drifts.
                **({"schema_sha256": schema_digest(schemas[name])} if isinstance(schemas.get(name), dict) else {}),
            }
            for name, tool in review.tools.items()
        },
        "blocked": dict(review.blocked),
    }


def build_policy() -> dict[str, Any]:
    """One entry per app whose enabled connector is served through the gateway."""
    from ctl.control_state import ControlState
    from ctl.mcp_catalog import load as load_catalog
    from ctl.registry import load as load_registry

    registry = load_registry()
    names = {service.id: service.name for service in registry.services}
    state = ControlState.runtime()
    apps: dict[str, Any] = {}
    for server in load_catalog(registry):
        runtime = state.mcp_server(server.id) if state else None
        if not server.gateway or not runtime or not runtime["enabled"] or runtime["state"] != "live":
            continue
        if server.service_id in apps:
            raise ValueError(f"{names[server.service_id]} has more than one enabled connector")
        apps[server.service_id] = app_policy(server, names[server.service_id])
    return {"version": 2, "apps": apps}


def _invalidate_policy(directory: Path) -> int:
    # Removing old-format authority also closes the rolling-upgrade window.
    # Refuse the mutation if invalidation cannot be persisted.
    (directory / "policy.json").unlink(missing_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return authority().invalidate()


@contextmanager
def policy_change():
    directory = project() / "policy"
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    with locked(directory / "policy.lock"):
        _invalidate_policy(directory)
        yield


def write_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    job_guard.checkpoint()
    directory = project() / "policy"
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    with locked(directory / "policy.lock"):
        try:
            candidate = dict(build_policy() if policy is None else policy)
        except (OSError, ValueError):
            _invalidate_policy(directory)
            raise
        candidate["version"] = 2
        candidate.pop("revision", None)
        current_path = directory / "policy.json"
        if current_path.exists():
            try:
                existing = json.loads(current_path.read_text())
                revision = existing.pop("revision")
                if existing == candidate:
                    payload = current_path.read_bytes()
                    authority().check_policy(revision, hashlib.sha256(payload).hexdigest())
                    return existing | {"revision": revision}
            except (ValueError, OSError, KeyError):
                pass
        revision = _invalidate_policy(directory)
        candidate["revision"] = revision
        validate_policy(candidate)
        payload = canonical(candidate).encode()
        write_atomic(current_path, payload)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        authority().publish(revision, hashlib.sha256(payload).hexdigest())
        return candidate


# --------------------------------------------------------------------------- lifecycle


def _materialize() -> Path:
    target = project()
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for name in ("Dockerfile", "docker-compose.yml", "gateway.py", "requirements.txt"):
        shutil.copy2(SOURCE / name, target / name)
    shutil.copy2(ROOT / "gateway_authority.py", target / "gateway_authority.py")
    (target / "authority").mkdir(mode=0o700, exist_ok=True)
    platform_releases.pin_compose(ROOT, SOURCE / "docker-compose.yml", target / "docker-compose.yml")
    compose_path = target / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    compose["services"]["mcp-gateway"]["build"] = {
        "context": ".",
        "dockerfile": "Dockerfile",
        "args": {"GATEWAY_SOURCE": "."},
    }
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False))
    (target / "logs").mkdir(mode=0o750, exist_ok=True)
    env = read_runtime_env(target / ".env")
    env.update({"MU3LAB_UID": str(os.getuid()), "MU3LAB_GID": str(os.getgid())})
    (target / ".env").write_text(runtime_env_text(env), encoding="utf-8")
    os.chmod(target / ".env", 0o600)
    return target


def _ensure_network(log) -> None:
    rc, _ = actions.docker_cmd(["docker", "network", "inspect", "mu3lab_mcp_upstream"], lambda _line: None)
    if rc:
        actions.docker_network_create("mu3lab_mcp_upstream", log)


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as response:
            status = json.loads(response.read(4096))
            expected = authority().policy_state()
            return (
                response.status == 200
                and status.get("revision") == expected["revision"]
                and status.get("policy_sha256") == expected["digest"]
                and expected["acknowledged"] == expected["revision"]
            )
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _build_gateway(target: Path, log) -> tuple[int, str]:
    """Development refreshes must not silently reuse an image with older gateway code."""
    if platform_releases.exact_tag(ROOT):
        return 0, ""  # _materialize selected the release's verified immutable image.
    with locked(target / ".build.lock"):
        digest = stamps.digest(
            target, [target / name for name in ("Dockerfile", "gateway.py", "gateway_authority.py", "requirements.txt")]
        )
        stamp = target / ".build.sha256"
        if stamps.read(stamp) == digest:
            return 0, ""
        rc, output = actions.docker_cmd(
            [
                "docker",
                "compose",
                "-f",
                str(target / "docker-compose.yml"),
                "--project-directory",
                str(target),
                "build",
                "mcp-gateway",
            ],
            log,
            timeout=300,
        )
        if rc == 0:
            stamps.write(stamp, digest)
        return rc, output


def refresh(log) -> tuple[bool, str]:
    """Write the current policy and make sure the gateway is running it."""
    job_guard.checkpoint()
    try:
        write_policy()
    except (OSError, ValueError) as exc:
        return False, f"Tool gateway policy could not be written: {exc}"
    target = _materialize()
    rc, output = _build_gateway(target, log)
    if rc:
        return False, "Tool gateway could not build the current code: " + output[-400:]
    _ensure_network(log)
    rc, output = actions.compose_up(target, log, wait_timeout=120)
    if rc:
        return False, "Tool gateway did not start: " + output[-400:]
    return (True, "Tool gateway is running.") if healthy() else (False, "Tool gateway did not pass its health check.")


def surface(service_id: str) -> list[dict[str, Any]]:
    """The tools the gateway shows this app's assistant right now, as LobeChat stores them."""
    from ctl.mcp_ops import _rpc_request

    url = LOCAL_ENDPOINT.format(service_id=service_id)
    token = app_token(service_id)
    _, session = _rpc_request(url, "initialize", 1, token=token)
    response, _ = _rpc_request(url, "tools/list", 2, token=token, session_id=session)
    policy = json.loads((project() / "policy" / "policy.json").read_text(encoding="utf-8"))
    reviewed = policy["apps"][service_id]["tools"]
    rows = []
    for tool in (response.get("result") or {}).get("tools", []):
        name = str(tool.get("name", ""))
        if name in META_TOOLS:
            risk = "write" if name == "change_with_tool" else "read"
        elif name in reviewed:
            risk = reviewed[name]["access"]
        else:
            continue
        rows.append(
            {
                "id": name,
                "title": str(tool.get("description") or name)[:240],
                "risk": risk,
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            }
        )
    return rows


def verify_operator(subject: str) -> None:
    """Refresh operator authority from Authentik, never caller-provided role text."""
    store = authority()
    try:
        allowed = subject in people.operator_subjects()
    except ValueError:
        store.revoke_subject(subject)
        raise ValueError("Operator membership could not be verified; connector access remains unavailable.") from None
    store.grant_subject(subject, operator=allowed)
    if not allowed:
        raise ValueError("Shared connector credentials are available only to current operators.")


def dispatch_operation(operation: str, subject: str) -> dict[str, Any]:
    """Execute an already approved immutable operation, with no argument override."""
    verify_operator(subject)
    record = authority().view(operation, subject)
    token = operator_token(subject, record["app"])
    url = f"http://127.0.0.1:{PORT}/apps/{record['app']}/operations/{operation}/execute"
    request = urllib.request.Request(
        url, data=b"{}", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=135) as response:
            result = json.loads(response.read(64_001))
        if not isinstance(result, dict) or not result.get("outcome"):
            raise ValueError(
                "Gateway could not establish a dispatch outcome. Check the operation status before retrying."
            )
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        # No POST retry: the gateway may have dispatched already. Its journal is authority.
        current = authority().view(operation, subject)
        return {
            "ok": current["state"] == "succeeded",
            "outcome": current["state"],
            "operation_id": operation,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Dispatch could not be confirmed. Refresh the operation and inspect the app before requesting another change.",
                    }
                ],
                "isError": True,
            },
        }


def current_policy() -> dict[str, Any]:
    payload = (project() / "policy" / "policy.json").read_bytes()
    policy = json.loads(payload)
    validate_policy(policy)
    authority().check_policy(policy["revision"], hashlib.sha256(payload).hexdigest())
    return policy
