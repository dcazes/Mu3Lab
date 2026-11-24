"""Home widgets for Speaches (manifest ``widgets``; ctl/app_widgets.py runs them).

Speech models (administrators): the speech-to-text and text-to-speech models voice uses, the voice it
speaks with, and every speech model downloaded on this server, from Mu3Lab's own voice settings
(``ctl.voice_settings``), so it shows while Speaches is stopped too. While Speaches runs, the models
it holds in memory now are marked, from its own list (``/api/ps``).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

PARTS = (("stt", "Speech to text"), ("tts", "Text to speech"))


def _loaded(ctx: WidgetContext) -> set[str]:
    """The models Speaches holds in memory now; none when it is stopped or does not say."""
    try:
        reply = ctx.get("/api/ps")
    except WidgetError:
        return set()
    # {"models": ["Systran/faster-whisper-small", ...]}
    models = (reply or {}).get("models") if isinstance(reply, dict) else []
    return {str(item) for item in models or [] if isinstance(item, str)}


def models(ctx: WidgetContext) -> dict[str, Any]:
    from ctl import voice_settings

    try:
        speech = voice_settings.speech_models(ctx.paths)
    except (KeyError, ValueError, OSError):
        raise WidgetError("Mu3Lab could not read the speech settings.") from None
    loaded = _loaded(ctx)
    stats, items = [], []
    for part, label in PARTS:
        options = speech[part]["options"]
        chosen = next((option for option in options if option["in_use"]), None)
        stats.append({"label": label, "value": chosen["name"] if chosen else "None chosen"})
        for option in options:
            if not option["downloaded"]:
                continue
            state = "Loaded" if option["id"] in loaded else "In use" if option["in_use"] else "Downloaded"
            items.append(
                {
                    "title": option["name"],
                    "subtitle": f"{label} · {option['precision']} · {option['download_gb']:g} GB",
                    "meta": state,
                    "tone": "good" if option["in_use"] else "",
                }
            )
    voice = speech.get("default_voice") or ""
    if voice:
        stats.append({"label": "Voice", "value": voice_settings._label(voice)})
    return {
        "stats": stats,
        "items_title": "Downloaded speech models",
        "items": items,
        "empty": "No speech models are downloaded yet.",
    }
