"""Small, fixed-surface Streamable HTTP MCP adapters for app data only."""

from __future__ import annotations

import base64
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

MODE = os.environ.get("MCP_APP", "")
APP_URL = os.environ.get("APP_URL", "").rstrip("/")
MAX_INPUT = 65_536
MAX_OUTPUT = 250_000


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def tool(name: str, description: str, parameters: dict) -> dict:
    return {"name": name, "description": description, "inputSchema": parameters}


TEXT = {"type": "string", "minLength": 1}
if MODE == "nextcloud":
    TOOLS = [
        tool("list_files", "List one Nextcloud folder", schema({"path": {"type": "string", "default": ""}})),
        tool("read_text_file", "Read one UTF-8 text file from Nextcloud", schema({"path": TEXT}, ["path"])),
        tool("write_text_file", "Create or replace one UTF-8 text file in Nextcloud", schema({"path": TEXT, "content": {"type": "string", "maxLength": 65536}}, ["path", "content"])),
    ]
elif MODE == "adventurelog":
    TOOLS = [
        tool("list_collections", "List AdventureLog collections", schema({})),
        tool("list_locations", "List AdventureLog locations", schema({})),
        tool("create_collection", "Create one AdventureLog collection", schema({"name": TEXT, "description": {"type": "string"}}, ["name"])),
    ]
else:
    raise SystemExit("unsupported adapter mode")


def _auth() -> str:
    if MODE == "nextcloud":
        username = os.environ.get("NEXTCLOUD_USERNAME", "")
        password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
        if not username or not password:
            raise ValueError("Nextcloud app credential is not configured")
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    token = os.environ.get("ADVENTURELOG_API_KEY", "")
    if not token:
        raise ValueError("AdventureLog API key is not configured")
    return "Bearer " + token


def _path(value: str) -> str:
    if not isinstance(value, str) or value.startswith("/") or "\\" in value or \
            any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("path must stay within the account's files")
    return "/".join(quote(part, safe="") for part in value.split("/") if part)


def _request(path: str, method: str = "GET", payload: bytes | None = None,
             content_type: str = "application/json") -> tuple[bytes, int]:
    headers = {"Authorization": _auth(), "Accept": "application/xml" if method == "PROPFIND" else "application/json"}
    if MODE == "nextcloud":
        headers["OCS-APIRequest"] = "true"
    if payload is not None:
        headers["Content-Type"] = content_type
    req = Request(APP_URL + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                raise ValueError("app response exceeded the size limit")
            return data, response.status
    except HTTPError as exc:
        raise ValueError(f"app returned HTTP {exc.code}") from None
    except URLError:
        raise ValueError("app is unavailable") from None


def _nextcloud(name: str, args: dict):
    user = quote(os.environ.get("NEXTCLOUD_USERNAME", ""), safe="")
    path = _path(args.get("path", ""))
    route = f"/remote.php/dav/files/{user}/" + path
    if name == "list_files":
        data, _ = _request(route, "PROPFIND", b"", "application/xml")
        root = ElementTree.fromstring(data)
        return [{"href": item.text} for item in root.findall(".//{DAV:}href") if item.text]
    if not path:
        raise ValueError("a file path is required")
    if name == "read_text_file":
        data, _ = _request(route)
        return {"content": data.decode("utf-8")}
    if name == "write_text_file":
        content = args.get("content", "")
        if not isinstance(content, str) or len(content.encode()) > MAX_INPUT:
            raise ValueError("content must be text under 64 KB")
        _, status = _request(route, "PUT", content.encode(), "text/plain; charset=utf-8")
        return {"saved": True, "status": status}
    raise ValueError("unknown tool")


def _adventurelog(name: str, args: dict):
    if name in {"list_collections", "list_locations"}:
        path = "/api/collections/" if name == "list_collections" else "/api/locations/"
        data, _ = _request(path)
        return json.loads(data)
    if name == "create_collection":
        value = {"name": args.get("name"), "description": args.get("description", "")}
        if not isinstance(value["name"], str) or not value["name"].strip():
            raise ValueError("name is required")
        data, _ = _request("/api/collections/", "POST", json.dumps(value).encode())
        return json.loads(data)
    raise ValueError("unknown tool")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._send(200, {"ok": bool(APP_URL)})

    def do_POST(self):
        if self.path != "/mcp":
            self.send_error(404)
            return
        token = os.environ.get("MCP_AUTH_TOKEN", "")
        if not token or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_INPUT:
                raise ValueError("request exceeds the size limit")
            request = json.loads(self.rfile.read(size))
            method = request.get("method", "")
            request_id = request.get("id")
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                          "serverInfo": {"name": f"mu3lab-{MODE}", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name", "")
                if name not in {item["name"] for item in TOOLS}:
                    raise ValueError("unknown tool")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                output = _nextcloud(name, args) if MODE == "nextcloud" else _adventurelog(name, args)
                result = {"content": [{"type": "text", "text": json.dumps(output)}]}
            else:
                raise ValueError("unsupported MCP method")
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._send(200, {"jsonrpc": "2.0", "id": locals().get("request_id"),
                             "error": {"code": -32000, "message": str(exc)[:200]}})

    def _send(self, status: int, value: dict):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
