"""Translate observations and workflow progress into one user-facing state."""

from __future__ import annotations

from typing import Literal

DisplayState = Literal["running", "stopped", "working", "checking", "not_installed", "needs_attention"]
WORKING = frozenset({"queued", "installing", "starting", "verifying", "uninstalling", "updating"})


def projection(item: dict) -> dict:
    state = str(item.get("state") or "unknown")
    installed = item.get("installation_state") in {"installed", "partial"}
    if state in WORKING:
        display: DisplayState = "working"
    elif state in {"ready", "running"}:
        display = "running"
    elif state in {"checking", "unknown"}:
        display = "checking"
    elif state == "stopped":
        display = "stopped"
    elif state in {"planned", "not_installed"}:
        display = "not_installed"
    else:
        display = "needs_attention"
    reason = str(item.get("detail") or "")
    if state == "config_required":
        reason = "Complete this app's settings before installing it."
    elif state == "needs_setup" and not (item.get("blocking_check") and reason):
        reason = "Finish this app's setup or sign-in before using it."
    return {"display_state": display, "reason": reason, "installed": bool(installed)}
