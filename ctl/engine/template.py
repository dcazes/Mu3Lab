"""``{{name}}`` placeholders in manifest values and app template files.

Names are either facts about this computer and app (``public_url``,
``dns_name``, ``https_port``, ``discovery_url``, ``data_root``, ``tz``, ``country_code``, ``currency_code``,
``origin``, ``sign_in_launch``) or another environment value of the same app (``{{DB_PASSWORD}}``),
or ``{{app:<id>:<ENV>}}`` for a value another app generated. An unknown name
is an error, so a typo never ships an empty setting.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_:.-]+)\s*\}\}")


class TemplateError(ValueError):
    """A placeholder names nothing Mu3Lab knows."""


Lookup = Callable[[str], str | None]


def render(text: str, lookup: Lookup) -> str:
    def replace(match: re.Match[str]) -> str:
        value = lookup(match.group(1))
        if value is None:
            raise TemplateError(f"unknown placeholder {{{{{match.group(1)}}}}}")
        return value

    return PLACEHOLDER.sub(replace, text)


def render_value(value: str | Mapping[str, Any] | list[Any], lookup: Lookup) -> str:
    """Render one env value; mappings and lists become compact JSON after rendering their strings."""
    if isinstance(value, str):
        return render(value, lookup)
    return json.dumps(_render_tree(value, lookup), separators=(",", ":"))


def _render_tree(value: Any, lookup: Lookup) -> Any:
    if isinstance(value, str):
        return render(value, lookup)
    if isinstance(value, Mapping):
        return {key: _render_tree(child, lookup) for key, child in value.items()}
    if isinstance(value, list):
        return [_render_tree(child, lookup) for child in value]
    return value


def names(value: Any) -> set[str]:
    """Every placeholder name used anywhere in ``value``."""
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value))
    if isinstance(value, Mapping):
        return set().union(*(names(child) for child in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(names(child) for child in value)) if value else set()
    return set()
