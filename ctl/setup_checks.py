"""Choices the owner must make before an installed app can be used.

A catalog app whose rules still wait on the owner (no outside provider connected and
no local model chosen, for example) installs normally and is then left
stopped, with the rule's sentence saying what to open. The sentence is kept
here so the app's card can show "Needs setup" with it; pressing Start checks
again, and starts the app once nothing is missing.
"""

from __future__ import annotations

from collections.abc import Sequence

from ctl.engine.hooks import HookContext, SetupNeeded
from ctl.rules import Rule
from ctl.runtime import RuntimePaths
from ctl.store import records

SCOPE = "setup_needed"


def first_missing(ctx: HookContext, rules: Sequence[Rule]) -> SetupNeeded | None:
    """The first choice still missing, or None when the app may start; real failures still raise."""
    for rule in rules:
        try:
            rule.setup_check(ctx)
        except SetupNeeded as missing:
            return missing
    return None


def remember(app_id: str, missing: SetupNeeded, paths: RuntimePaths) -> None:
    records.put(SCOPE, app_id, {"stage": missing.stage, "code": missing.code, "message": missing.message}, paths)


def forget(app_id: str, paths: RuntimePaths) -> None:
    records.delete(SCOPE, app_id, paths)


def waiting(app_id: str, paths: RuntimePaths | None = None) -> str:
    """The sentence saying what the owner should open, or "" when the app waits on nothing."""
    return str(records.get(SCOPE, app_id, paths or RuntimePaths()).get("message") or "")
