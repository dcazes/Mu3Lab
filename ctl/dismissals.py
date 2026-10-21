"""Home-page warnings each person has dismissed, on every device they use.

The dashboard names each warning by what it says and when the problem began, so a
dismissal lasts only while the same problem continues: a changed warning no longer
matches and shows again. Only the most recent dismissals are kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import records

SCOPE = "dismissed-warnings"
LIMIT = 100


def _name(person: Mapping[str, Any]) -> str:
    return str(person.get("subject_id") or person.get("username") or "")


def for_person(person: Mapping[str, Any], paths: RuntimePaths = RuntimePaths()) -> list[str]:
    name = _name(person)
    if not name:
        return []
    items = records.get(SCOPE, name, paths).get("items")
    return [str(item) for item in items] if isinstance(items, list) else []


def dismiss(person: Mapping[str, Any], warning: str, paths: RuntimePaths = RuntimePaths()) -> list[str]:
    """Hide ``warning`` for this person; returns everything they have dismissed."""
    name = _name(person)
    if not name:
        return []
    items = [item for item in for_person(person, paths) if item != warning] + [warning]
    items = items[-LIMIT:]
    records.put(SCOPE, name, {"items": items}, paths)
    return items
