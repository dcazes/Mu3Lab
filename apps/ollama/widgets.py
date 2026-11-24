"""Home widgets for Ollama (manifest ``widgets``; ctl/app_widgets.py runs them).

Local AI shows which models are loaded in memory now, how much graphics memory each takes, and
when Ollama will unload it, beside how many models are downloaded.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, until

GB = 1024**3


def _size(count: float) -> str:
    return f"{count / GB:.1f} GB"


def local_ai(ctx: WidgetContext) -> dict[str, Any]:
    loaded = ctx.get("/api/ps").get("models") or []
    downloaded = ctx.get("/api/tags").get("models") or []
    memory = sum(int(model.get("size_vram") or 0) for model in loaded)
    items = []
    for model in loaded:
        on_gpu = int(model.get("size_vram") or 0)
        total = int(model.get("size") or 0)
        where = "graphics memory" if on_gpu >= total else f"memory ({_size(on_gpu)} on the graphics card)"
        items.append(
            {
                "title": str(model.get("name") or "A model"),
                "subtitle": f"Uses {_size(total)} of {where}",
                "meta": f"Unloads {until(model['expires_at'])}" if model.get("expires_at") else "",
                "progress": on_gpu / total if total else None,
            }
        )
    return {
        "stats": [
            {"label": "Loaded", "value": str(len(loaded))},
            {"label": "Graphics memory", "value": _size(memory) if loaded else "0 GB"},
            {"label": "Downloaded", "value": str(len(downloaded))},
        ],
        "items_title": "In memory now",
        "items": items,
        "empty": "No model is loaded; the first question loads one.",
    }
