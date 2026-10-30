"""The household's voice assistant choices, and the settings file the voice agent reads.

An administrator chooses, in Settings, Voice Assistant:

- **Responsiveness**: how long the assistant waits when someone sounds unfinished (``fast``,
  ``balanced`` or ``patient``); the agent turns the word into seconds.
- **Interruptions**: whether talking over the assistant stops it.
- **Voice**: the assistant's own voice; empty uses Speaches' default voice.
- **Web search**: on unless switched off; it works once the SearXNG app is installed.

The agent cannot reach the control plane, so Mu3Lab writes ``settings.json`` beside the app tools
(``ctl/voice_tools.py``) whenever these change and whenever chat's assistants sync (every install
does). The agent reads it at each call's start, so a change needs no restart. The file also says
how the assistant talks, which only the full-duplex switch changes (``ctl/duplex_voice.py``).

The same page lists the voices Speaches has, plays a sample of one, and checks each app the
assistant can use, as the administrator looking.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import records

Log = Callable[[str], None]

SETTINGS = ("voice", "settings")
FILE = "settings.json"
# What earlier versions wrote instead; deleted when FILE is written.
OLD_FILE = "engine.json"
RESPONSIVENESS = ("fast", "balanced", "patient")
DEFAULTS: dict[str, Any] = {"responsiveness": "balanced", "interruptions": True, "voice": "", "search": True}
# Kokoro voice names: a language and gender letter pair, then a name (af_heart, bm_george).
VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z0-9]{1,30}$")
SEARCH_CAPABILITY = "web_search"
# Where the agent reaches SearXNG on Mu3Lab's backend network (apps/searxng).
SEARCH_URL = "http://searxng:8080"
# The voice assistant speaks and hears English (the agent's speech recognition and turn detector).
VOICE_LANGUAGES = ("en-us", "en-gb")
SAMPLE = "Hi, I'm your Mu3Lab assistant. How can I help?"
SPEECH_SECONDS = 60
CHECK_SECONDS = 15


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def validate(new: dict[str, Any]) -> dict[str, Any]:
    """The settings as stored; raises ValueError with a sentence for the person."""
    if new.get("responsiveness") not in RESPONSIVENESS:
        raise ValueError("Choose how quickly the voice assistant answers.")
    voice = str(new.get("voice") or "")
    if voice and not VOICE_NAME.match(voice):
        raise ValueError("Choose one of the voices listed.")
    return {
        "responsiveness": new["responsiveness"],
        "interruptions": bool(new.get("interruptions")),
        "voice": voice,
        "search": bool(new.get("search")),
    }


def get(paths: RuntimePaths | None = None) -> dict[str, Any]:
    stored = records.get(*SETTINGS, paths or RuntimePaths()) or {}
    try:
        clean = validate({**DEFAULTS, **stored})
    except ValueError:
        clean = dict(DEFAULTS)
    return {
        **clean,
        "changed_at": str(stored.get("changed_at") or ""),
        "changed_by": str(stored.get("changed_by") or ""),
    }


def save(changes: dict[str, Any], actor: str, log: Log, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Store the changed settings and hand them to the agent for its next call."""
    paths = paths or RuntimePaths()
    current = get(paths)
    chosen = validate({**current, **{key: value for key, value in changes.items() if value is not None}})
    records.put(*SETTINGS, {**chosen, "changed_at": _now(), "changed_by": actor}, paths)
    write(log, paths)
    return get(paths)


# --- Web search --------------------------------------------------------------------------


def search_app():
    from ctl.platform_apps import by_capability

    return by_capability(SEARCH_CAPABILITY)


def search_installed() -> bool:
    from ctl import chat_ops

    try:
        return chat_ops.installed(search_app().id)
    except ValueError:
        return False


# --- The agent's settings file ----------------------------------------------------------


def agent_settings(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """What the agent reads at each call's start (apps/livekit/voice-agent/src/agent.py)."""
    from ctl import duplex_voice

    paths = paths or RuntimePaths()
    chosen = get(paths)
    return {
        "engine": duplex_voice.agent_engine(paths),
        "omni_url": duplex_voice.OMNI_URL,
        "responsiveness": chosen["responsiveness"],
        "interruptions": chosen["interruptions"],
        "voice": chosen["voice"],
        "search_url": SEARCH_URL if chosen["search"] and search_installed() else "",
    }


def write(log: Log, paths: RuntimePaths | None = None) -> bool:
    """Write the agent's settings file; returns whether it changed. Does nothing before LiveKit is installed."""
    from ctl import voice_tools

    paths = paths or RuntimePaths()
    target = voice_tools.folder(paths)
    if not target.parent.is_dir():
        return False
    text = json.dumps(agent_settings(paths), indent=1, sort_keys=True)
    # The agent runs as its own user: the folder and file must be readable by it (see voice_tools).
    target.mkdir(mode=0o755, exist_ok=True)
    os.chmod(target, 0o755)
    (target / OLD_FILE).unlink(missing_ok=True)
    path = target / FILE
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        os.chmod(path, 0o644)
        return False
    partial = target / f".{FILE}.partial"
    partial.write_text(text, encoding="utf-8")
    os.chmod(partial, 0o644)
    partial.replace(path)
    log("The voice assistant's settings were updated.")
    return True


# --- Voices, through Speaches on this server -------------------------------------------------


def _speech_app():
    from ctl.platform_apps import by_capability

    return by_capability("speech")


def _speech_url(path: str) -> str:
    return f"http://127.0.0.1:{_speech_app().manifest.service.local_port}{path}"


def speech_models() -> dict[str, Any]:
    """Speaches' speech recognition and voice models: the choices and the ones in use."""
    from ctl import chat_ops, service_config
    from ctl.registry import load

    app = _speech_app()
    service = load().get(app.id)
    fields = {str(field["key"]): field for field in service_config.read(service)}

    def field(key: str) -> dict[str, Any]:
        item = fields.get(key) or {}
        return {
            "value": str(item.get("value") or item.get("default") or ""),
            "options": [str(option) for option in item.get("options") or []],
        }

    return {
        "app_id": app.id,
        "installed": chat_ops.installed(app.id),
        "stt": field("stt_model"),
        "tts": field("tts_model"),
        "default_voice": str((fields.get("tts_voice") or {}).get("value") or ""),
    }


def _label(name: str) -> str:
    """``af_heart`` reads as "Heart"."""
    return name.partition("_")[2].replace("_", " ").title() or name


def voices() -> list[dict[str, str]]:
    """The English voices Speaches can speak with now, or [] when it is not answering."""
    import httpx

    try:
        reply = httpx.get(_speech_url("/v1/audio/voices"), timeout=CHECK_SECONDS)
        reply.raise_for_status()
        found = reply.json().get("voices") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    seen: dict[str, dict[str, str]] = {}
    for voice in found:
        name, language = str(voice.get("name") or ""), str(voice.get("language") or "").lower()
        if not VOICE_NAME.match(name) or language not in VOICE_LANGUAGES or name in seen:
            continue
        seen[name] = {
            "id": name,
            "name": _label(name),
            "accent": "British" if language == "en-gb" else "American",
            "gender": str(voice.get("gender") or ""),
        }
    return sorted(seen.values(), key=lambda voice: (voice["accent"], voice["name"]))


def sample(voice: str) -> str:
    """One spoken sentence in ``voice``, as base64 MP3; raises ValueError with a sentence."""
    import httpx

    if not VOICE_NAME.match(voice):
        raise ValueError("Choose one of the voices listed.")
    model = speech_models()["tts"]["value"]
    try:
        reply = httpx.post(
            _speech_url("/v1/audio/speech"),
            json={"model": model, "voice": voice, "input": SAMPLE, "response_format": "mp3"},
            timeout=SPEECH_SECONDS,
        )
        reply.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Speaches could not read the sample aloud: {exc}") from exc
    return base64.b64encode(reply.content).decode("ascii")


# --- Is each app's assistant reachable? ------------------------------------------------------


def check_tools(email: str, paths: RuntimePaths | None = None) -> dict[str, dict[str, Any]]:
    """For each app the voice assistant would hand this person to: whether its tools answer, and how many.

    Uses the person's own entry in the agent's file, so it checks exactly what a call would use.
    """
    from ctl import contextforge, mcp_ops, voice_tools

    paths = paths or RuntimePaths()
    try:
        people = json.loads((voice_tools.folder(paths) / voice_tools.FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    person = (people.get("people") or {}).get(email.lower()) or {}
    local = f"http://127.0.0.1:{contextforge.app().manifest.service.local_port}"
    found: dict[str, dict[str, Any]] = {}
    for agent in person.get("agents") or []:
        url = str(agent.get("url") or "").replace(contextforge.INTERNAL, local, 1)
        token = str(agent.get("token") or "")
        try:
            _, session = mcp_ops._rpc_request(url, "initialize", 1, token=token)
            mcp_ops._rpc_notification(url, "notifications/initialized", token=token, session_id=session)
            reply, _ = mcp_ops._rpc_request(url, "tools/list", 2, token=token, session_id=session)
            tools = (reply.get("result") or {}).get("tools") or []
            found[str(agent["id"])] = {
                "ok": bool(tools),
                "tools": len(tools),
                # ContextForge answers for a stopped app too, with none of its tools.
                "detail": "" if tools else "No tools loaded. Start the app in Apps, then check again.",
            }
        except (OSError, ValueError) as exc:
            found[str(agent["id"])] = {"ok": False, "tools": 0, "detail": _reason(exc)}
    return found


def _reason(exc: Exception) -> str:
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {401, 403}:
            return "ContextForge refused the key; it is renewed at the next sync."
        return f"ContextForge answered {exc.code}."
    if isinstance(exc, urllib.error.URLError | ConnectionError | TimeoutError):
        return "ContextForge is not answering."
    return str(exc)[:200]
