"""Mu3Lab tool gateway: one small, policy-checked MCP surface per app.

LobeChat connects each app assistant to /apps/<app>/mcp with that app's own
token.  The gateway forwards to the app's active connector, exposes only the
everyday tools directly, and lets the assistant discover and run the rest
through find_tools, use_tool and change_with_tool.  The control plane owns
policy.json (reviews plus the owner's switches); the gateway re-reads it when
it changes and never widens it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gateway_authority import Authority, schema_digest, validate_arguments
from gateway_authority import validate_policy as _validate_policy

POLICY_PATH = os.environ.get("GATEWAY_POLICY", "/config/policy.json")
CALL_LOG = os.environ.get("GATEWAY_CALL_LOG", "/logs/calls.jsonl")
PROTOCOL = "2025-03-26"
MAX_REQUEST = 256_000
MAX_UPSTREAM = 4_000_000
MAX_TEXT = 60_000
CALL_LOG_LIMIT = 5_000_000
TOOL_CACHE_SECONDS = 600
SEARCH_LIMIT = 8
# Request shape and resource bounds. A household gateway serves a handful of
# people; these limits keep one slow client, one slow connector or one runaway
# assistant from exhausting it for everyone else.
MAX_DEPTH = 32
MAX_ID_LENGTH = 128
MAX_CONTENT_ITEMS = 50
MAX_CONNECTIONS = 32
MAX_ACTIVE_PER_PRINCIPAL = 4
SOCKET_TIMEOUT = 10
BODY_DEADLINE = 10
REQUEST_DEADLINE = 90
WRITE_TIMEOUT = 120
CONNECTOR_WAIT = 30
# (burst, refill per second) for every request, and separately for tool calls.
REQUEST_RATE = (40, 2.0)
CALL_RATE = (15, 0.5)
FIND, USE, CHANGE = "find_tools", "use_tool", "change_with_tool"
META_TOOLS = (FIND, USE, CHANGE)
_WORD = re.compile(r"[a-z0-9]+")


class GatewayError(Exception):
    """A problem the assistant should read and act on, returned as a tool error."""


class UnknownOutcome(GatewayError):
    """Dispatch happened without a reliable reply; never repeat it automatically."""


class ConnectorRefused(GatewayError):
    """A definitive connector error reply."""


class RpcError(Exception):
    """A malformed or refused request, answered with a deterministic JSON-RPC error."""

    def __init__(self, code: int, message: str, *, status: int = 200, retry_after: int = 0):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


PARSE_ERROR, INVALID_REQUEST, INVALID_PARAMS = -32700, -32600, -32602


def _too_deep(value: object) -> bool:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_DEPTH:
            return True
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return False


def parse_message(raw: bytes) -> tuple[object, str, dict]:
    """Validate one JSON-RPC 2.0 envelope; return its id, method and params."""
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RpcError(PARSE_ERROR, "The request is not valid JSON.", status=400) from None
    if not isinstance(message, dict):
        raise RpcError(INVALID_REQUEST, "Send one JSON-RPC request object; batches are not supported.", status=400)
    if _too_deep(message):
        raise RpcError(INVALID_REQUEST, f"The request is nested more than {MAX_DEPTH} levels deep.", status=400)
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0":
        raise RpcError(INVALID_REQUEST, "The request must declare jsonrpc 2.0.", status=400)
    if request_id is not None and (
        type(request_id) not in (int, str) or (isinstance(request_id, str) and len(request_id) > MAX_ID_LENGTH)
    ):
        raise RpcError(INVALID_REQUEST, "The request id must be a short string or an integer.", status=400)
    method = message.get("method")
    if not isinstance(method, str) or not method or len(method) > MAX_ID_LENGTH:
        raise RpcError(INVALID_REQUEST, "The request method must be a short string.", status=400)
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "'params' must be an object.")
    return request_id, method, params


class RateLimiter:
    """Token buckets per principal; only bounded, non-secret keys are kept."""

    def __init__(self, burst: int, per_second: float, *, limit: int = 1000, clock=time.monotonic):
        self.burst, self.per_second, self.limit, self.clock = burst, per_second, limit, clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> int:
        """Spend one token; return 0, or the whole seconds to wait before retrying."""
        with self._lock:
            now = self.clock()
            tokens, stamp = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - stamp) * self.per_second)
            if len(self._buckets) >= self.limit and key not in self._buckets:
                self._evict(now)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return max(1, int((1 - tokens) / self.per_second + 0.999))
            self._buckets[key] = (tokens - 1, now)
            return 0

    def _evict(self, now: float) -> None:
        # Refilled buckets carry no state worth keeping; past that, forget the least recent.
        kept = {
            key: (tokens, stamp)
            for key, (tokens, stamp) in self._buckets.items()
            if tokens + (now - stamp) * self.per_second < self.burst
        }
        if len(kept) >= self.limit:
            kept = dict(sorted(kept.items(), key=lambda item: item[1][1])[len(kept) - self.limit + 1 :])
        self._buckets = kept


class Capacity:
    """Concurrent requests per principal, plus counters for readiness diagnostics."""

    def __init__(self, per_principal: int = MAX_ACTIVE_PER_PRINCIPAL):
        self.per_principal = per_principal
        self._lock = threading.Lock()
        self._active: dict[str, int] = {}
        self.counters = {"rate_limited": 0, "busy": 0, "connections_refused": 0}

    def enter(self, key: str) -> bool:
        with self._lock:
            if self._active.get(key, 0) >= self.per_principal:
                self.counters["busy"] += 1
                return False
            self._active[key] = self._active.get(key, 0) + 1
            return True

    def leave(self, key: str) -> None:
        with self._lock:
            remaining = self._active.get(key, 1) - 1
            if remaining > 0:
                self._active[key] = remaining
            else:
                self._active.pop(key, None)

    def count(self, name: str) -> None:
        with self._lock:
            self.counters[name] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"active": sum(self._active.values()), **self.counters}


REQUESTS = RateLimiter(*REQUEST_RATE)
CALLS = RateLimiter(*CALL_RATE)
CAPACITY = Capacity()
# The whole-request deadline of the request this thread is serving.
_REQUEST = threading.local()


def _deadline() -> float:
    return getattr(_REQUEST, "deadline", 0.0) or time.monotonic() + REQUEST_DEADLINE


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("request deadline reached")
    return left


AUTHORITY = Authority(os.environ.get("GATEWAY_AUTHORITY", "/authority"), initialize=False)


# --------------------------------------------------------------------------- policy


class PolicyStore:
    def __init__(self, path: str, authority: Authority | None = None):
        self.path = path
        directory = Path(path).parent
        self.authority = authority or Authority(
            (directory.parent if directory.name == "policy" else directory) / "authority", initialize=False
        )
        self._lock = threading.Lock()
        self._digest = ""
        self._policy: dict = {"apps": {}}

    def get(self) -> dict:
        # Read every time: mtime cannot establish that a revocation was published,
        # and a cached grant is never authority after a missing/unreadable file.
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    payload = handle.read(MAX_UPSTREAM + 1)
                if len(payload) > MAX_UPSTREAM:
                    raise ValueError("gateway policy is too large")
                policy = json.loads(payload)
                _validate_policy(policy)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                self.authority.check_policy(policy["revision"], digest, acknowledge=True)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self._policy, self._digest = {"apps": {}}, ""
                CONNECTORS.forget()
                if isinstance(exc, (TypeError, KeyError)):
                    raise ValueError("gateway policy is malformed") from None
                raise
            digest = hashlib.sha256(payload.encode()).hexdigest()
            if digest != self._digest:
                self._policy, self._digest = policy, digest
                CONNECTORS.forget()
            return self._policy


POLICY = PolicyStore(POLICY_PATH, AUTHORITY)


# --------------------------------------------------------------------------- upstream MCP client


def _decode(payload: bytes, content_type: str, request_id: object = None) -> dict:
    """Return the JSON-RPC reply to ``request_id``.

    A Streamable HTTP stream may carry notifications and server requests
    before the reply; only the message answering this request counts.
    """
    text = payload.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
        messages = []
        for event in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(line[5:].removeprefix(" ") for line in event.splitlines() if line.startswith("data:"))
            if not data.strip():
                continue
            try:
                messages.append(json.loads(data))
            except ValueError:
                continue
        replies = [
            message
            for message in messages
            if isinstance(message, dict)
            and ("result" in message or "error" in message)
            and (request_id is None or message.get("id") == request_id)
        ]
        if not replies:
            raise GatewayError("The connector's stream did not contain a reply to this request.")
        return replies[-1]
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise GatewayError("The connector returned an unexpected response.")
    if request_id is not None and "id" in decoded and decoded["id"] != request_id:
        raise GatewayError("The connector answered a different request.")
    return decoded


def _read(response, deadline: float) -> bytes:
    """Read at most MAX_UPSTREAM bytes, giving up at the request deadline."""
    chunks, size = [], 0
    while True:
        _remaining(deadline)
        chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_UPSTREAM:
            raise GatewayError("The connector's answer was too large; ask for fewer results.")
        chunks.append(chunk)


class Connector:
    """A Streamable HTTP client for one app's active connector."""

    def __init__(self, upstream: dict):
        self.url = str(upstream["url"])
        self.headers = {str(key): str(value) for key, value in (upstream.get("headers") or {}).items()}
        self.session = ""
        self.tools: dict[str, dict] = {}
        self.tools_at = 0.0
        self._lock = threading.Lock()
        self._next_id = 0

    def _post(self, body: dict, *, expect_reply: bool = True, timeout: float | None = None) -> tuple[dict, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            **self.headers,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        deadline = time.monotonic() + timeout if timeout else _deadline()
        with urlopen(request, timeout=min(_remaining(deadline), WRITE_TIMEOUT)) as response:
            session = response.headers.get("Mcp-Session-Id", "") or self.session
            payload = _read(response, deadline)
            if not expect_reply or not payload.strip():
                return {}, session
            return _decode(payload, response.headers.get("Content-Type", ""), body.get("id")), session

    def _initialize(self) -> None:
        self.session = ""
        _reply, session = self._post(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "mu3lab-gateway", "version": "1"},
                },
            }
        )
        self.session = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)

    def request(self, method: str, params: dict, *, retry_safe: bool = False, before_dispatch=None) -> dict:
        """Retry only operations whose caller explicitly declares safe to repeat."""
        if not self._lock.acquire(timeout=min(CONNECTOR_WAIT, max(0.0, _deadline() - time.monotonic()))):
            raise GatewayError("The connector is busy with other requests; try again shortly.")
        try:
            return self._request(method, params, retry_safe=retry_safe, before_dispatch=before_dispatch)
        finally:
            self._lock.release()

    def _request(self, method: str, params: dict, *, retry_safe: bool, before_dispatch) -> dict:
        for attempt in (1, 2):
            dispatched = False
            try:
                if not self.session:
                    self._initialize()
                if before_dispatch:
                    before_dispatch()
                self._next_id += 1
                dispatched = True
                reply, _ = self._post(
                    {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
                    # A change is dispatched once; give it the full upstream time.
                    timeout=None if retry_safe else WRITE_TIMEOUT,
                )
                if reply.get("error"):
                    raise ConnectorRefused("The connector refused the request; inspect the app for details.")
                result = reply.get("result")
                if not isinstance(result, dict) and not retry_safe:
                    raise UnknownOutcome(
                        "The connector returned no reliable outcome. Do not repeat the change automatically."
                    )
                return result if isinstance(result, dict) else {}
            except (HTTPError, URLError, OSError) as exc:
                if isinstance(exc, HTTPError):
                    exc.close()
                self.session = ""
                if dispatched and not retry_safe:
                    raise UnknownOutcome(
                        "The connector call's outcome is unknown. It was not retried; check the app before trying again."
                    ) from None
                recoverable = not isinstance(exc, HTTPError) or exc.code in {400, 404}
                if attempt == 1 and recoverable:
                    continue
                if isinstance(exc, HTTPError):
                    raise GatewayError(f"The connector answered HTTP {exc.code}.") from None
                raise GatewayError("The connector is not reachable right now.") from None
            except ConnectorRefused:
                raise
            except (ValueError, GatewayError):
                if dispatched and not retry_safe:
                    raise UnknownOutcome(
                        "The connector reply could not establish the outcome. Check the app before another attempt."
                    ) from None
                raise
        raise GatewayError("The connector is not reachable right now.")

    def list_tools(self) -> dict[str, dict]:
        if self.tools and time.monotonic() - self.tools_at < TOOL_CACHE_SECONDS:
            return self.tools
        tools: dict[str, dict] = {}
        cursor = None
        for _page in range(20):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}, retry_safe=True)
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    tools[str(tool["name"])] = tool
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools, self.tools_at = tools, time.monotonic()
        return tools


class ConnectorPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._connectors: dict[str, tuple[str, Connector]] = {}

    def forget(self) -> None:
        with self._lock:
            self._connectors.clear()

    def get(self, app_id: str, app: dict) -> Connector:
        key = json.dumps(app["upstream"], sort_keys=True)
        principal = app.get("_principal", {})
        app_id = f"{app_id}:{principal.get('subject')}:{principal.get('provider')}:{principal.get('version')}"
        with self._lock:
            current = self._connectors.get(app_id)
            if current is None or current[0] != key:
                current = (key, Connector(app["upstream"]))
                self._connectors[app_id] = current
            return current[1]


CONNECTORS = ConnectorPool()


# --------------------------------------------------------------------------- the per-app tool surface


def _switch_hint(app: dict) -> str:
    return f"The owner can switch it on in Mu3Lab: Settings, Chat integrations, {app['name']}."


def _categories(app: dict) -> dict[str, dict]:
    return {str(category["id"]): category for category in app["categories"]}


def _reviewed(app: dict, name: str) -> dict:
    if name in app.get("blocked", {}):
        raise GatewayError(f"'{name}' is not available in Mu3Lab: {app['blocked'][name]}")
    tool = app["tools"].get(name)
    if tool is None:
        raise GatewayError(f"There is no tool called '{name}'. Call {FIND} to see what is available.")
    return tool


def _check_enabled(app: dict, name: str, tool: dict) -> None:
    category = _categories(app)[tool["category"]]
    if not category["enabled"]:
        raise GatewayError(
            f"'{name}' is in the {category['title']} category, which is switched off. {_switch_hint(app)}"
        )
    if not tool["enabled"]:
        kind = "changes data and is" if tool["access"] == "write" else "is"
        raise GatewayError(f"'{name}' {kind} switched off. {_switch_hint(app)}")


def _drifted(tool: dict, upstream: dict | None) -> bool:
    """Whether the connector now describes this tool differently from when Mu3Lab verified it."""
    pinned = tool.get("schema_sha256")
    return bool(pinned and upstream is not None and schema_digest(upstream.get("inputSchema")) != pinned)


def _offered(app: dict, upstream: dict[str, dict]) -> dict[str, dict]:
    """Upstream tools that are reviewed and still match their verified input schema."""
    return {
        name: tool for name, tool in upstream.items() if name in app["tools"] and not _drifted(app["tools"][name], tool)
    }


DRIFT_NOTE = "The connector changed this tool since Mu3Lab verified it. Re-verify the connector in Mu3Lab first."


def _enabled(app: dict, tool: dict) -> bool:
    return bool(
        (tool["access"] == "read" or app.get("approval_required", False))
        and tool["enabled"]
        and _categories(app)[tool["category"]]["enabled"]
    )


def _describe(upstream: dict | None) -> str:
    text = " ".join(str((upstream or {}).get("description") or "").split())
    return text[:280] + ("…" if len(text) > 280 else "")


def _meta_tools(app: dict) -> list[dict]:
    category_ids = [str(category["id"]) for category in app["categories"]]
    tools = [
        {
            "name": FIND,
            "description": (
                f"Find more {app['name']} tools. Call with no arguments to list every category, including ones that "
                "are switched off. Pass a category to list its tools with their inputs, or a query to search tool "
                "names and descriptions. The tools you can see directly are only the most common ones."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": category_ids, "description": "Category to list."},
                    "query": {"type": "string", "description": "Words describing what you want to do."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": USE,
            "description": (
                f"Run a {app['name']} tool that only reads data, by the name {FIND} returned. "
                "Pass that tool's inputs as arguments."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": f"Tool name from {FIND}."},
                    "arguments": {"type": "object", "description": "The tool's inputs."},
                },
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
    ]
    if app.get("approval_required"):
        tools.append(
            {
                "name": CHANGE,
                "description": "Request a change. Mu3Lab returns a link where the operator must approve the exact inputs; this call does not execute the change.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
                    "required": ["tool"],
                    "additionalProperties": False,
                },
            }
        )
    return tools


def list_tools(app_id: str, app: dict) -> list[dict]:
    upstream = _offered(app, CONNECTORS.get(app_id, app).list_tools())
    direct = [
        {
            "name": name,
            "description": str(upstream[name].get("description") or name),
            "inputSchema": upstream[name].get("inputSchema") or {"type": "object", "properties": {}},
        }
        for name, tool in app["tools"].items()
        if tool.get("core") and _enabled(app, tool) and name in upstream
    ]
    return direct + _meta_tools(app)


def find_tools(app_id: str, app: dict, arguments: dict) -> dict:
    available = CONNECTORS.get(app_id, app).list_tools()
    upstream = _offered(app, available)
    categories = _categories(app)
    category_id = arguments.get("category")
    query = str(arguments.get("query") or "").strip()
    if category_id is None and not query:
        overview = []
        for category in app["categories"]:
            members = [tool for tool in app["tools"].values() if tool["category"] == category["id"]]
            entry = {
                "category": category["id"],
                "title": category["title"],
                "what": category["summary"],
                "state": "on" if category["enabled"] else "off",
                "tools_on": sum(_enabled(app, tool) and tool["name"] in upstream for tool in members),
                "tools_off": sum(not _enabled(app, tool) for tool in members),
            }
            if not category["enabled"]:
                entry["note"] = _switch_hint(app)
            overview.append(entry)
        return {"categories": overview, "next": f"Call {FIND} with a category to see its tools and inputs."}
    if category_id is not None and category_id not in categories:
        raise GatewayError(f"Unknown category '{category_id}'. Call {FIND} with no arguments to list them.")
    candidates = [tool for tool in app["tools"].values() if category_id is None or tool["category"] == category_id]
    if query:
        words = set(_WORD.findall(query.lower()))

        def score(tool: dict) -> int:
            text = f"{tool['name'].replace('_', ' ')} {_describe(upstream.get(tool['name']))} "
            text += categories[tool["category"]]["title"]
            return len(words & set(_WORD.findall(text.lower())))

        ranked = sorted(((score(tool), tool) for tool in candidates), key=lambda item: -item[0])
        candidates = [tool for points, tool in ranked if points][:SEARCH_LIMIT]
    listed = []
    for tool in candidates:
        category = categories[tool["category"]]
        on = _enabled(app, tool) and tool["name"] in upstream
        entry = {
            "tool": tool["name"],
            "category": category["id"],
            "what": _describe(upstream.get(tool["name"])),
            "changes_data": tool["access"] == "write",
            "state": "on" if on else "off",
            "run_with": (CHANGE if tool["access"] == "write" else USE) if on else None,
        }
        if on:
            entry["inputs"] = upstream[tool["name"]].get("inputSchema") or {"type": "object", "properties": {}}
        elif tool["name"] in available and tool["name"] not in upstream:
            entry["note"] = DRIFT_NOTE
        elif tool["name"] not in upstream:
            entry["note"] = "The connector does not offer this tool right now."
        elif tool["access"] == "write":
            entry["note"] = "Chat writes are unavailable until Mu3Lab can verify approval for each call."
        else:
            entry["note"] = _switch_hint(app)
        listed.append(entry)
    if not listed:
        return {"tools": [], "note": f"Nothing matched. Call {FIND} with no arguments to browse categories."}
    return {"tools": listed}


def call_tool(app_id: str, app: dict, name: str, arguments: dict, *, via: str) -> dict:
    tool = _reviewed(app, name)
    if via == USE and tool["access"] != "read":
        raise GatewayError(f"'{name}' changes data; chat writes are unavailable until approval can be verified.")
    if via == CHANGE and tool["access"] != "write":
        raise GatewayError(f"'{name}' only reads data; run it with {USE}.")
    _check_enabled(app, name, tool)
    if tool["access"] == "write" and (not app.get("approval_required") or not app.get("_principal")):
        raise GatewayError("Chat writes are unavailable until Mu3Lab can verify approval for each call.")
    connector = CONNECTORS.get(app_id, app)
    offered = connector.list_tools().get(name)
    if offered is None:
        raise GatewayError(f"The connector does not offer '{name}' right now.")
    if _drifted(tool, offered):
        raise GatewayError(DRIFT_NOTE)
    schema = offered.get("inputSchema") or {"type": "object"}
    try:
        validate_arguments(schema, arguments)
        if tool["access"] == "write":
            operation = AUTHORITY.prepare(app["_principal"], app_id, app, name, arguments, schema, app["_revision"])
            url = f"{app.get('approval_origin', '')}/tool-approvals/{operation['id']}"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"This change awaits your approval in Mu3Lab: [Review the exact change]({url}). Nothing has been dispatched.",
                    }
                ],
                "structuredContent": {
                    "operation_id": operation["id"],
                    "state": operation["state"],
                    "approval_url": url,
                },
                "isError": False,
            }
    except ValueError as exc:
        raise GatewayError(str(exc)) from None
    result = connector.request("tools/call", {"name": name, "arguments": arguments}, retry_safe=True)
    return _bounded(result)


def execute_operation(app_id: str, app: dict, operation: str) -> dict:
    principal = app["_principal"]
    record = AUTHORITY.view(operation, principal["subject"], arguments=True)
    if record["state"] in {"succeeded", "failed", "outcome_unknown", "dispatching", "rejected", "revoked", "expired"}:
        return {
            "ok": record["state"] == "succeeded",
            "outcome": record["state"],
            "operation_id": operation,
            "result": {},
        }
    connector = CONNECTORS.get(app_id, app)
    offered = connector.list_tools().get(record["tool"])
    if not offered:
        raise GatewayError("The connector no longer offers this tool.")
    if _drifted(app["tools"].get(record["tool"], {}), offered):
        raise GatewayError(DRIFT_NOTE)
    schema = offered.get("inputSchema") or {"type": "object"}

    def authorize():
        current = POLICY.get()
        selected = current["apps"].get(app_id)
        if not selected or AUTHORITY.claim(operation, principal, selected, schema, current["revision"]) is None:
            raise GatewayError("This request was revoked or already attempted.")

    try:
        result = connector.request(
            "tools/call",
            {"name": record["tool"], "arguments": record["arguments"]},
            retry_safe=False,
            before_dispatch=authorize,
        )
        outcome = "failed" if result.get("isError") else "succeeded"
        AUTHORITY.finish(operation, outcome)
        return {"ok": outcome == "succeeded", "outcome": outcome, "operation_id": operation, "result": _bounded(result)}
    except UnknownOutcome:
        AUTHORITY.finish(operation, "outcome_unknown")
        return {
            "ok": False,
            "outcome": "outcome_unknown",
            "operation_id": operation,
            "result": _text("The outcome is unknown. Check the app before requesting another change.", error=True),
        }
    except ConnectorRefused:
        AUTHORITY.finish(operation, "failed")
        return {
            "ok": False,
            "outcome": "failed",
            "operation_id": operation,
            "result": _text("The connector refused the change.", error=True),
        }


def _bounded(result: dict) -> dict:
    """Cap what one tool result can put into a chat: text, other content and structure."""
    content = []
    budget = MAX_TEXT
    items = result.get("content") if isinstance(result.get("content"), list) else []
    omitted = max(0, len(items) - MAX_CONTENT_ITEMS)
    for item in items[:MAX_CONTENT_ITEMS]:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text", ""))
            if len(text) > budget:
                text = text[:budget] + "\n[Result shortened by Mu3Lab; ask for fewer results or a narrower search.]"
            budget = max(0, budget - len(text))
            content.append({"type": "text", "text": text})
            continue
        size = len(json.dumps(item))
        if size > budget or _too_deep(item):
            omitted += 1
            continue
        budget -= size
        content.append(item)
    if omitted:
        content.append({"type": "text", "text": f"[Mu3Lab left out {omitted} oversized result item(s).]"})
    bounded = {"content": content, "isError": bool(result.get("isError"))}
    structured = result.get("structuredContent")
    if structured is not None and not _too_deep(structured) and len(json.dumps(structured)) <= MAX_TEXT:
        bounded["structuredContent"] = structured
    return bounded


def _text(value: object, *, error: bool = False) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle_call(app_id: str, app: dict, name: str, arguments: dict) -> tuple[dict, str, str]:
    """Run one tools/call. Returns the MCP result, the underlying tool name and how it was reached."""
    if name == FIND:
        return _text(find_tools(app_id, app, arguments)), FIND, "find"
    if name in (USE, CHANGE):
        target = arguments.get("tool")
        inner = arguments.get("arguments") or {}
        if not isinstance(target, str) or not target:
            raise GatewayError(f"Pass the tool name from {FIND} as 'tool'.")
        if not isinstance(inner, dict):
            raise GatewayError("'arguments' must be an object with the tool's inputs.")
        return call_tool(app_id, app, target, inner, via=name), target, name
    tool = app["tools"].get(name)
    if tool is None or not tool.get("core"):
        raise GatewayError(f"There is no tool called '{name}' here. Call {FIND} to see what is available.")
    via = CHANGE if tool["access"] == "write" else USE
    return call_tool(app_id, app, name, arguments, via=via), name, "direct"


def _log_call(app_id: str, app: dict, tool: str, via: str, outcome: str, started: float) -> None:
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": app_id,
        "server": app.get("server_id", ""),
        "subject": app.get("_principal", {}).get("subject", ""),
        "credential_version": app.get("_principal", {}).get("version", 0),
        "provider": app.get("_principal", {}).get("provider", ""),
        "operation_id": app.get("_operation_id", ""),
        "tool": tool[:100],
        "via": via,
        "outcome": outcome,
        "ms": int((time.monotonic() - started) * 1000),
    }
    try:
        if os.path.getsize(CALL_LOG) > CALL_LOG_LIMIT:
            os.replace(CALL_LOG, CALL_LOG + ".1")
    except OSError:
        pass
    try:
        with open(CALL_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Applies to every socket read, headers included, so a silent client is dropped.
    timeout = SOCKET_TIMEOUT

    def do_GET(self):
        if self.path == "/live":
            # Liveness: the process answers. Readiness (policy, capacity) is /health.
            self._send(200, {"ok": True})
            return
        if self.path == "/health":
            try:
                policy = POLICY.get()
                apps = len(policy["apps"])
            except (OSError, ValueError):
                self._send(503, {"ok": False, "reason": "policy_unavailable", "capacity": self.server_capacity()})
                return
            self._send(
                200,
                {
                    "ok": True,
                    "apps": apps,
                    "revision": policy["revision"],
                    "policy_sha256": POLICY._digest,
                    "capacity": self.server_capacity(),
                },
            )
            return
        self._send(405 if self._app_id() else 404, {"error": "not found"})

    def server_capacity(self) -> dict:
        connections = getattr(self.server, "connections", None)
        return CAPACITY.snapshot() | (connections() if connections else {})

    def do_DELETE(self):
        # Stateless server: there is no session to end.
        self._send(405 if self._app_id() else 404, {"error": "not supported"})

    def _app_id(self) -> str:
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) == 3 and parts[0] == "apps" and parts[2] == "mcp":
            return parts[1]
        if (
            len(parts) == 5
            and parts[0] == "apps"
            and parts[2] == "operations"
            and parts[4] == "execute"
            and re.fullmatch(r"[0-9a-f]{32}", parts[3])
        ):
            return parts[1]
        return ""

    def _authorized(self, app: dict) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        try:
            app["_principal"] = AUTHORITY.authenticate(header.removeprefix("Bearer "), app["_app_id"])
            return True
        except (ValueError, OSError, KeyError):
            return False

    def _body(self) -> bytes:
        """Read the declared body within one deadline, however slowly it trickles in."""
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if size < 1 or size > MAX_REQUEST:
            raise RpcError(INVALID_REQUEST, "Request is empty or too large.", status=413 if size > 0 else 400)
        deadline = time.monotonic() + BODY_DEADLINE
        chunks, received = [], 0
        while received < size:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RpcError(INVALID_REQUEST, "The request body arrived too slowly.", status=408)
            self.connection.settimeout(min(left, SOCKET_TIMEOUT))
            try:
                chunk = self.rfile.read1(size - received)
            except TimeoutError:
                raise RpcError(INVALID_REQUEST, "The request body arrived too slowly.", status=408) from None
            if not chunk:
                raise RpcError(INVALID_REQUEST, "The request body ended early.", status=400)
            chunks.append(chunk)
            received += len(chunk)
        self.connection.settimeout(SOCKET_TIMEOUT)
        return b"".join(chunks)

    def do_POST(self):
        app_id = self._app_id()
        try:
            policy = POLICY.get()
            selected = policy["apps"].get(app_id) if app_id else None
            app = dict(selected) | {"_app_id": app_id, "_revision": policy["revision"]} if selected else None
        except (OSError, ValueError):
            self._send(503, {"error": "gateway policy unavailable"})
            return
        if app is None:
            self._send(404, {"error": "not found"})
            return
        if not self._authorized(app):
            self._send(401, {"error": "unauthorized"})
            return
        principal = app["_principal"]
        key = f"{principal['subject']}:{principal['provider']}:{app_id}"
        wait = REQUESTS.take(key)
        if wait:
            CAPACITY.count("rate_limited")
            self._send_error(None, RpcError(-32000, "Too many requests; slow down.", status=429, retry_after=wait))
            return
        if not CAPACITY.enter(key):
            self._send_error(
                None, RpcError(-32000, "Too many requests are already running.", status=429, retry_after=2)
            )
            return
        _REQUEST.deadline = time.monotonic() + REQUEST_DEADLINE
        try:
            self._dispatch(app_id, app, key)
        finally:
            _REQUEST.deadline = 0.0
            CAPACITY.leave(key)

    def _dispatch(self, app_id: str, app: dict, key: str) -> None:
        request_id = None
        try:
            if "/operations/" in self.path:
                if app["_principal"]["provider"] == "control-discovery":
                    self._send(403, {"error": "discovery credential cannot execute changes"})
                    return
                operation = self.path.split("/")[4]
                started = time.monotonic()
                result = execute_operation(app_id, app, operation)
                record = AUTHORITY.view(operation, app["_principal"]["subject"])
                app["_operation_id"] = operation
                _log_call(app_id, app, record["tool"], "approved_operation", result["outcome"], started)
                self._send(200, result)
                return
            request_id, method, params = parse_message(self._body())
            if app["_principal"]["provider"] == "control-discovery" and method not in {
                "initialize",
                "notifications/initialized",
                "ping",
                "tools/list",
            }:
                raise GatewayError("This credential permits tool discovery only.")
            if request_id is None:
                self._send(202, None)
                return
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": f"mu3lab-{app_id}", "version": "1"},
                    "instructions": str(app.get("instructions", "")),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": list_tools(app_id, app)}
            elif method == "tools/call":
                wait = CALLS.take(key)
                if wait:
                    CAPACITY.count("rate_limited")
                    raise RpcError(-32000, "Too many tool calls; slow down.", status=429, retry_after=wait)
                result = self._call(app_id, app, params)
            else:
                raise RpcError(-32601, f"Method not found: {method}")
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except RpcError as exc:
            self._send_error(request_id, exc)
        except (GatewayError, ValueError) as exc:
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}})
        except (TypeError, KeyError):
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "bad request"}})

    def _call(self, app_id: str, app: dict, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name or len(name) > MAX_ID_LENGTH:
            raise RpcError(INVALID_PARAMS, "'name' must be the tool's name.")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "'arguments' must be an object.")
        started = time.monotonic()
        try:
            result, tool, via = handle_call(app_id, app, name, arguments)
        except GatewayError as exc:
            _log_call(app_id, app, str(arguments.get("tool") or name), name, "refused", started)
            return _text(str(exc), error=True)
        outcome = (
            "awaiting_approval"
            if (result.get("structuredContent") or {}).get("state") in {"pending", "approved"}
            else "error"
            if result.get("isError")
            else "ok"
        )
        _log_call(app_id, app, tool, via, outcome, started)
        return result

    def _send_error(self, request_id, error: RpcError) -> None:
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else {}
        self._send(
            error.status,
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": str(error)}},
            headers,
        )

    def _send(self, status: int, value: dict | None, headers: dict | None = None) -> None:
        body = b"" if value is None else json.dumps(value).encode()
        self.send_response(status)
        if value is not None:
            self.send_header("Content-Type", "application/json")
        for name, header in (headers or {}).items():
            self.send_header(name, header)
        if status >= 400 and status not in {401, 404, 405}:
            # A refused or malformed request leaves the stream in an unknown state.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class BoundedServer(ThreadingHTTPServer):
    """A threaded server that refuses connections beyond a fixed number at once."""

    daemon_threads = True

    def __init__(self, address, handler, max_connections: int = MAX_CONNECTIONS):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_connections)
        self._max = max_connections
        self._open = 0
        self._count_lock = threading.Lock()

    def connections(self) -> dict:
        with self._count_lock:
            return {"connections": self._open, "max_connections": self._max}

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            CAPACITY.count("connections_refused")
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 2\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with self._count_lock:
            self._open += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release()

    def _release(self) -> None:
        with self._count_lock:
            self._open -= 1
        self._slots.release()


def main() -> None:
    AUTHORITY.recover_dispatches()
    BoundedServer(("0.0.0.0", int(os.environ.get("GATEWAY_PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
