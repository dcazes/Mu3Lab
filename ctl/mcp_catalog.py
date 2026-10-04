"""Reviewed chat connectors, read from each app's ``connectors/`` folder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ctl.registry import Registry

ROOT = Path(__file__).resolve().parent.parent


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
    auto_provision: bool = False
    auto_provision_note: str = ""
    review_note: str = ""
    reviewed_update: dict[str, str] | None = None
    # A checked-in tool review; connectors with one are served through the tool gateway.
    review: str = ""
    secrets: tuple[dict[str, Any], ...] = ()
    include: tuple[str, ...] = ()

    @property
    def gateway(self) -> bool:
        return bool(self.review)

    def compose_path(self, root: Path = ROOT) -> Path | None:
        if not self.compose_dir:
            return None
        path = (root / self.compose_dir).resolve()
        if root.resolve() not in path.parents:
            raise ValueError("MCP compose path escapes the checkout")
        return path


def for_service(servers: tuple[McpServer, ...], service_id: str) -> list[McpServer]:
    """An app's reviewed connectors, default first."""
    return sorted(
        (server for server in servers if server.service_id == service_id and server.status == "accepted"),
        key=lambda server: not server.preferred,
    )


def load(registry: Registry) -> tuple[McpServer, ...]:
    """Every reviewed connector of every app in the catalog."""
    result: list[McpServer] = []
    for app in registry.catalog.apps:
        for connector in app.connectors:
            folder = app.connector_folder(connector.id).relative_to(ROOT)
            update = connector.reviewed_update
            result.append(
                McpServer(
                    id=connector.id,
                    service_id=app.id,
                    name=connector.name,
                    status="accepted",
                    preferred=connector.preferred,
                    provenance=connector.provenance,
                    repository=str(connector.repository),
                    revision=connector.revision,
                    transport=connector.transport,
                    endpoint=str(connector.endpoint),
                    local_health=str(connector.local_health),
                    compose_dir=str(folder),
                    credentials=tuple(field.model_dump() for field in connector.credentials),
                    auto_provision=connector.auto_provision,
                    auto_provision_note=connector.provision_note,
                    review_note=connector.review_note,
                    reviewed_update=(
                        {
                            "version": update.version,
                            "compose_dir": update.folder,
                            "release_url": str(update.release_url),
                        }
                        if update
                        else None
                    ),
                    review=str(folder / "review.yaml") if connector.reviewed else "",
                    secrets=tuple(secret.model_dump() for secret in connector.secrets),
                    include=connector.include,
                )
            )
    return tuple(result)
