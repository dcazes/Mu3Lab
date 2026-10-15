"""Checked-in tool reviews: what each connector tool does and how it is offered.

A review is the only source for a tool's category, whether it reads or changes
data, and whether it is an everyday tool the assistant sees directly.  A tool a
connector offers that its review does not list is unreviewed and never exposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACCESS = frozenset({"read", "write"})
MAX_CORE_TOOLS = 6
_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")
_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,99}")


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    summary: str
    default_on: bool


@dataclass(frozen=True)
class ReviewedTool:
    name: str
    category: str
    access: str
    core: bool


@dataclass(frozen=True)
class Review:
    server_id: str
    revision: str
    reviewed_at: str
    guidance: str
    categories: tuple[Category, ...]
    tools: dict[str, ReviewedTool]
    blocked: dict[str, str]
    data_scope: str = "operator_only"
    delegation: str = "none"

    def category(self, category_id: str) -> Category | None:
        return next((item for item in self.categories if item.id == category_id), None)


def path_for(relative: str, root: Path = ROOT) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("review path escapes the checkout")
    return path


def load(server_id: str, relative: str, root: Path = ROOT) -> Review:
    raw = yaml.safe_load(path_for(relative, root).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or raw.get("server") != server_id:
        raise ValueError(f"review for {server_id} has the wrong server or schema_version")
    if raw.get("data_scope", "operator_only") != "operator_only" or raw.get("delegation", "none") != "none":
        raise ValueError(f"review for {server_id} uses a delegation scope not yet supported")
    categories: list[Category] = []
    for item in raw.get("categories") or []:
        if not isinstance(item, dict) or not _ID.fullmatch(str(item.get("id", ""))):
            raise ValueError(f"review for {server_id} has an invalid category id")
        if not item.get("title") or not item.get("summary"):
            raise ValueError(f"review for {server_id}: category {item['id']} needs a title and summary")
        categories.append(
            Category(
                id=str(item["id"]),
                title=str(item["title"]),
                summary=str(item["summary"]).strip(),
                default_on=bool(item.get("default_on", True)),
            )
        )
    category_ids = [item.id for item in categories]
    if not categories or len(set(category_ids)) != len(category_ids):
        raise ValueError(f"review for {server_id} needs unique categories")
    tools: dict[str, ReviewedTool] = {}
    for name, item in (raw.get("tools") or {}).items():
        if not _TOOL.fullmatch(str(name)) or not isinstance(item, dict):
            raise ValueError(f"review for {server_id} has an invalid tool entry {name!r}")
        if item.get("category") not in category_ids or item.get("access") not in ACCESS:
            raise ValueError(f"review for {server_id}: tool {name} needs a known category and read/write access")
        tools[str(name)] = ReviewedTool(
            name=str(name), category=str(item["category"]), access=str(item["access"]), core=bool(item.get("core"))
        )
    if not tools:
        raise ValueError(f"review for {server_id} lists no tools")
    blocked = {str(name): str(reason).strip() for name, reason in (raw.get("blocked") or {}).items()}
    if any(not reason for reason in blocked.values()):
        raise ValueError(f"review for {server_id}: every blocked tool needs a reason")
    if set(blocked) & set(tools):
        raise ValueError(f"review for {server_id} both offers and blocks the same tool")
    if sum(tool.core for tool in tools.values()) > MAX_CORE_TOOLS:
        raise ValueError(f"review for {server_id} has more than {MAX_CORE_TOOLS} everyday tools")
    unused = set(category_ids) - {tool.category for tool in tools.values()}
    if unused:
        raise ValueError(f"review for {server_id} has empty categories: {', '.join(sorted(unused))}")
    return Review(
        server_id=server_id,
        revision=str(raw.get("revision", "")),
        reviewed_at=str(raw.get("reviewed_at", "")),
        guidance=str(raw.get("guidance", "")).strip(),
        categories=tuple(categories),
        tools=tools,
        blocked=blocked,
    )
