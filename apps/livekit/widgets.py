"""Home widgets for LiveKit (manifest ``widgets``; ctl/app_widgets.py runs them).

Voice calls (administrators): calls going on now, calls today, and the last few calls, with who
called, when and for how long. LiveKit keeps no history, so the voice agent notes each call's start
and end in ``calls/calls.jsonl`` in the app's folder (``note_call`` in voice-agent/src/agent.py):
times and who only, never what was said. Calls now come from LiveKit's own list of rooms, signed with
the app's API key, while it runs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, ago

LOG = Path("calls") / "calls.jsonl"
PEOPLE = Path("voice-tools") / "people.json"
# The end of the log is enough for today and the last calls.
TAIL_BYTES = 200_000
LAST = 5
# A call with no end noted (the agent stopped mid-call) is shown as ended after this long.
LONGEST_SECONDS = 4 * 3600
ROOMS_PORT = 7880


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token(key: str, secret: str) -> str:
    """A short-lived LiveKit token that may list rooms (HS256, as LiveKit's server SDKs make)."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    claims = _b64(json.dumps({"iss": key, "nbf": now - 10, "exp": now + 60, "video": {"roomList": True}}).encode())
    signature = hmac.new(secret.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    return f"{header}.{claims}.{_b64(signature)}"


def _rooms_now(ctx: WidgetContext) -> int | None:
    """How many calls are open now; None when LiveKit is stopped or does not answer."""
    env = ctx.env()
    key, secret = env.get("LIVEKIT_API_KEY", ""), env.get("LIVEKIT_API_SECRET", "")
    if not key or not secret:
        return None
    try:
        reply = ctx.request(
            "POST",
            "/twirp/livekit.RoomService/ListRooms",
            port=ROOMS_PORT,
            json={},
            headers={"Authorization": f"Bearer {_token(key, secret)}"},
        )
    except WidgetError:
        return None
    return sum(1 for room in (reply or {}).get("rooms") or [] if int(room.get("num_participants") or 0) > 0)


def _lines(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(size - TAIL_BYTES, 0))
            text = handle.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    except OSError:
        raise WidgetError("Mu3Lab could not read the call log.") from None
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # the first line of a tail, cut in half
        if isinstance(row, dict) and row.get("room") and isinstance(row.get("at"), int | float):
            rows.append(row)
    return rows


def _names(ctx: WidgetContext) -> dict[str, str]:
    try:
        people = json.loads((ctx.paths.projects / ctx.app.id / PEOPLE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(email): str(person.get("name") or "") for email, person in (people.get("people") or {}).items()}


def _length(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def calls(ctx: WidgetContext) -> dict[str, Any]:
    folder = ctx.paths.projects / ctx.app.id
    found: dict[str, dict[str, Any]] = {}
    for row in _lines(folder / LOG):
        call = found.setdefault(str(row["room"]), {"room": row["room"]})
        if row.get("event") == "start":
            call["start"], call["who"] = float(row["at"]), str(row.get("who") or "")
        elif row.get("event") == "end":
            call["end"] = float(row["at"])
    now = time.time()
    finished = sorted(
        (call for call in found.values() if "start" in call), key=lambda call: call["start"], reverse=True
    )
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = [call for call in finished if call["start"] >= midnight]
    names = _names(ctx)
    live = _rooms_now(ctx)
    if live is None:
        live = sum(1 for call in finished if "end" not in call and now - call["start"] < LONGEST_SECONDS)

    def item(call: dict[str, Any]) -> dict[str, Any]:
        who = names.get(call["who"]) or call["who"].split("@")[0] or "Someone"
        going = "end" not in call and now - call["start"] < LONGEST_SECONDS
        end = call.get("end") or min(now, call["start"] + LONGEST_SECONDS)
        # The length beside the time, which shows at every size.
        return {
            "title": who,
            "meta": f"on a call · {_length(now - call['start'])}"
            if going
            else f"{_length(end - call['start'])} · {ago(call['start'])}",
            "tone": "good" if going else "",
        }

    return {
        "stats": [
            {"label": "On a call now", "value": str(live), "tone": "good" if live else ""},
            {"label": "Calls today", "value": str(len(today))},
            {
                "label": "Time today",
                "value": _length(
                    sum(min(call.get("end") or now, call["start"] + LONGEST_SECONDS) - call["start"] for call in today)
                )
                if today
                else "0 min",
            },
        ],
        "items_title": "Last calls",
        "items": [item(call) for call in finished[:LAST]],
        "empty": "No voice calls yet.",
    }
