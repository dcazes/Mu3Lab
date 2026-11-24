"""Voice call reports: what was said on each call, for administrators to find what went wrong.

The voice agent saves one folder per call in the voice app's ``reports`` folder (``save_report`` in
``apps/livekit/voice-agent/src/agent.py``): ``call.json`` (who, when), ``report.json`` (LiveKit's
session report) and, unless switched off, ``audio.ogg``. The household chooses in Settings, Voice
assistant whether reports and their audio are kept, and for how many days (``ctl.voice_settings``).

They hold what people said at home, so only administrators see them, each opening is noted in the
activity log, they are never backed up, and they go when the voice app is uninstalled.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ctl.runtime import RuntimePaths

FOLDER = "reports"
# <UTC time>_<room>, as the agent names them; nothing else is ever read or deleted.
REPORT_ID = re.compile(r"^\d{8}-\d{6}Z_[A-Za-z0-9_-]{1,120}$")
# Audio sent to the dashboard at most this large (about an hour of speech).
MAX_AUDIO_BYTES = 40 * 1024 * 1024
TOOL_OUTPUT_CHARS = 600


class ReportError(ValueError):
    """The report cannot be shown; the message is for the person."""


def folder(paths: RuntimePaths | None = None) -> Path:
    from ctl import voice_tools

    return (paths or RuntimePaths()).projects / voice_tools.app().id / FOLDER


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def prune(days: int, paths: RuntimePaths | None = None, now: float | None = None) -> int:
    """Delete reports older than ``days`` (the agent does too, after each call)."""
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    base = folder(paths)
    for report in base.iterdir() if base.is_dir() else []:
        if REPORT_ID.match(report.name) and report.is_dir() and report.stat().st_mtime < cutoff:
            shutil.rmtree(report, ignore_errors=True)
            removed += 1
    return removed


def listing(paths: RuntimePaths | None = None) -> list[dict[str, Any]]:
    """Every kept report, newest first, after deleting the ones past the household's limit."""
    from ctl import voice_settings

    paths = paths or RuntimePaths()
    prune(int(voice_settings.get(paths)["reports_days"]), paths)
    base = folder(paths)
    found = []
    for report in sorted(base.iterdir() if base.is_dir() else [], reverse=True):
        if not (REPORT_ID.match(report.name) and report.is_dir()):
            continue
        about = _read_json(report / "call.json")
        started = _float(about.get("started_at")) or report.stat().st_mtime
        ended = _float(about.get("ended_at")) or started
        found.append(
            {
                "id": report.name,
                "who": str(about.get("name") or about.get("who") or "Someone"),
                "started_at": started,
                "seconds": max(0.0, ended - started),
                "audio": (report / "audio.ogg").is_file(),
            }
        )
    return found


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _path(report_id: str, paths: RuntimePaths | None) -> Path:
    if not REPORT_ID.match(report_id):
        raise ReportError("That is not a call report.")
    path = folder(paths) / report_id
    if not path.is_dir():
        raise ReportError("That call report is gone: it was deleted, or kept for fewer days.")
    return path


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part for part in content if isinstance(part, str))
    return ""


def read(report_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """One call: who and when, then each message and tool call in order, with each reply's timing."""
    path = _path(report_id, paths)
    about = _read_json(path / "call.json")
    report = _read_json(path / "report.json")
    lines = []
    for item in (report.get("chat_history") or {}).get("items") or []:
        kind = item.get("type")
        if kind == "message" and item.get("role") in ("user", "assistant"):
            metrics = item.get("metrics") or {}
            lines.append(
                {
                    "kind": "said",
                    "who": "person" if item["role"] == "user" else "assistant",
                    "text": _text(item.get("content")),
                    "interrupted": bool(item.get("interrupted")),
                    "at": _float(item.get("created_at")),
                    # Seconds from the end of the person's speech to the reply's first sound.
                    "reply_after": _float(metrics.get("e2e_latency")) or None,
                }
            )
        elif kind == "function_call":
            lines.append(
                {
                    "kind": "tool",
                    "who": "assistant",
                    "text": f"{item.get('name') or 'tool'}({item.get('arguments') or ''})",
                    "at": _float(item.get("created_at")),
                }
            )
        elif kind == "function_call_output":
            output = str(item.get("output") or "")
            lines.append(
                {
                    "kind": "tool_error" if item.get("is_error") else "tool_result",
                    "who": "assistant",
                    "text": output[:TOOL_OUTPUT_CHARS] + ("…" if len(output) > TOOL_OUTPUT_CHARS else ""),
                    "at": _float(item.get("created_at")),
                }
            )
    summary = next((entry for entry in listing(paths) if entry["id"] == report_id), None) or {}
    return {
        "id": report_id,
        "who": summary.get("who") or str(about.get("who") or "Someone"),
        "started_at": summary.get("started_at") or 0.0,
        "seconds": summary.get("seconds") or 0.0,
        "audio": (path / "audio.ogg").is_file(),
        "lines": lines,
    }


def audio(report_id: str, paths: RuntimePaths | None = None) -> str:
    """The call's audio as a data address the dashboard can play and save."""
    file = _path(report_id, paths) / "audio.ogg"
    if not file.is_file():
        raise ReportError("This call has no audio: it was kept without it.")
    if file.stat().st_size > MAX_AUDIO_BYTES:
        raise ReportError("This call's audio is too long to play here.")
    return "data:audio/ogg;base64," + base64.b64encode(file.read_bytes()).decode("ascii")


def delete(report_id: str, paths: RuntimePaths | None = None) -> None:
    shutil.rmtree(_path(report_id, paths))
