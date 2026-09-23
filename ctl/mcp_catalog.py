"""Validated MCP candidates limited to applications in the Mu3Lab registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ctl.registry import Registry

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "mcp-catalog.yaml"
STATUSES = frozenset({"accepted", "review_required", "rejected", "not_available"})
TRANSPORTS = frozenset({"streamable-http", "openapi-bridge", "stdio", "none"})


@dataclass(frozen=True)
class McpServer:
    id: str
    service_id: str
    name: str
    status: str
    preferred: bool
    provenance: str
    repository: str
    revision: str
    transport: str
    endpoint: str
    local_health: str
    compose_dir: str
    credentials: tuple[dict[str, Any], ...]
    review_note: str = ""
    reviewed_update: dict[str, str] | None = None

    def compose_path(self, root: Path = ROOT) -> Path | None:
        if not self.compose_dir:
            return None
        path = (root / self.compose_dir).resolve()
        if root.resolve() not in path.parents:
            raise ValueError("MCP compose path escapes the checkout")
        return path


def load(registry: Registry, path: Path = CATALOG) -> tuple[McpServer, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("MCP catalog schema_version must be 1")
    excluded = set(raw.get("excluded_services", []))
    known = {service.id for service in registry.services}
    if not excluded.issubset(known) or "vaultwarden" not in excluded:
        raise ValueError("MCP exclusion list is incomplete")
    result: list[McpServer] = []
    ids: set[str] = set()
    preferred: set[str] = set()
    for item in raw.get("servers", []):
        if not isinstance(item, dict):
            raise ValueError("MCP catalog entries must be mappings")
        server_id, service_id = str(item.get("id", "")), str(item.get("service_id", ""))
        if not server_id or server_id in ids or service_id not in known or service_id in excluded:
            raise ValueError("MCP catalog contains an invalid server or application")
        if item.get("status") not in STATUSES or item.get("transport") not in TRANSPORTS:
            raise ValueError(f"MCP server {server_id} has invalid review metadata")
        if bool(item.get("preferred")):
            if service_id in preferred:
                raise ValueError(f"MCP application {service_id} has multiple preferred servers")
            preferred.add(service_id)
        credentials = item.get("credentials", [])
        if not isinstance(credentials, list) or any(
                not isinstance(field, dict) or field.get("type") not in {"string", "secret"}
                for field in credentials):
            raise ValueError(f"MCP server {server_id} has invalid credential fields")
        compose_dir = str(item.get("compose_dir", ""))
        if compose_dir.startswith("/") or ".." in Path(compose_dir).parts:
            raise ValueError(f"MCP server {server_id} has unsafe compose_dir")
        update = item.get("reviewed_update")
        if update is not None:
            if not isinstance(update, dict) or not all(update.get(key) for key in ("version", "compose_dir", "release_url")):
                raise ValueError(f"MCP server {server_id} has invalid reviewed update")
            candidate_dir = str(update["compose_dir"])
            if Path(candidate_dir).is_absolute() or ".." in Path(candidate_dir).parts:
                raise ValueError(f"MCP server {server_id} has unsafe update path")
        result.append(McpServer(
            id=server_id, service_id=service_id, name=str(item.get("name", server_id)),
            status=str(item["status"]), preferred=bool(item.get("preferred")),
            provenance=str(item.get("provenance", "")), repository=str(item.get("repository", "")),
            revision=str(item.get("revision", "")), transport=str(item["transport"]),
            endpoint=str(item.get("endpoint", "")), local_health=str(item.get("local_health", "")),
            compose_dir=compose_dir, credentials=tuple(dict(field) for field in credentials),
            review_note=str(item.get("review_note", "")),
            reviewed_update={str(key): str(value) for key, value in update.items()} if update else None,
        ))
        ids.add(server_id)
    return tuple(result)
