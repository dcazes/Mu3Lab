"""What the Home widgets show that comes from outside Mu3Lab: weather, places, stock prices, answers.

The server fetches these, not the browser, so phones away from home need no extra access and
only coordinates and ticker symbols leave the house: never who is asking.

- Weather and place search: Open-Meteo (free, no account or key).
- Stock prices: Yahoo Finance's public chart data (no account or key).
- Ask: the household's own chat model through LiteLLM, told what every installed app is for,
  the same guide the main chat assistant has.

Answers are kept a few minutes, so a board open on several screens asks once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from ctl.manifest.catalog import cached
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

AGENT = {"User-Agent": "Mu3Lab home widgets"}
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
PLACES_URL = "https://geocoding-api.open-meteo.com/v1/search"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
WEATHER_SECONDS = 600
STOCK_SECONDS = 60
PLACE_SECONDS = 86400
ASK_MODEL = "mu3lab-chat"
ASK_SECONDS = 90
MAX_QUESTION = 2000

_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_lock = threading.Lock()


class WidgetError(RuntimeError):
    """The outside service or the chat model did not answer; the message is for the person."""


def _remember(key: tuple[Any, ...], seconds: int, fetch: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    value = fetch()
    with _lock:
        _cache[key] = (now, value)
        if len(_cache) > 500:  # a long-running server should not keep every place ever searched
            for stale in sorted(_cache, key=lambda item: _cache[item][0])[:250]:
                _cache.pop(stale, None)
    return value


def _get(url: str, params: dict[str, Any], what: str) -> Any:
    try:
        response = httpx.get(url, params=params, headers=AGENT, timeout=10)
    except httpx.HTTPError:
        raise WidgetError(f"{what} could not be reached. Check this server's internet connection.") from None
    if response.status_code == 404:
        return None
    if not response.is_success:
        raise WidgetError(f"{what} did not answer (HTTP {response.status_code}). Try again in a minute.")
    return response.json()


def weather(latitude: float, longitude: float, units: str) -> dict[str, Any]:
    """Now and the next seven days at one place, in the place's own time zone."""
    fahrenheit = units == "fahrenheit"
    key = ("weather", round(latitude, 2), round(longitude, 2), fahrenheit)

    def fetch() -> dict[str, Any]:
        data = _get(
            WEATHER_URL,
            {
                "latitude": round(latitude, 4),
                "longitude": round(longitude, 4),
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
                "wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
                "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
                "wind_speed_unit": "mph" if fahrenheit else "kmh",
            },
            "The weather service",
        )
        if not data:
            raise WidgetError("The weather service has no forecast for this place.")
        current, daily = data.get("current") or {}, data.get("daily") or {}
        days = [
            {
                "date": date,
                "code": int(daily["weather_code"][index] or 0),
                "high": daily["temperature_2m_max"][index],
                "low": daily["temperature_2m_min"][index],
                "rain_chance": (daily.get("precipitation_probability_max") or [None] * 7)[index],
            }
            for index, date in enumerate(daily.get("time") or [])
        ]
        return {
            "ok": True,
            "units": "fahrenheit" if fahrenheit else "celsius",
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind": current.get("wind_speed_10m"),
            "wind_unit": "mph" if fahrenheit else "km/h",
            "code": int(current.get("weather_code") or 0),
            "is_day": bool(current.get("is_day", 1)),
            "days": days,
        }

    return _remember(key, WEATHER_SECONDS, fetch)


def places(query: str) -> list[dict[str, Any]]:
    """Towns and cities matching a name, for choosing the weather widget's place."""
    name = query.strip()[:80]
    if len(name) < 2:
        return []

    def fetch() -> list[dict[str, Any]]:
        data = _get(PLACES_URL, {"name": name, "count": 6, "language": "en", "format": "json"}, "Place search")
        found = []
        for item in (data or {}).get("results") or []:
            where = ", ".join(str(part) for part in (item.get("admin1"), item.get("country")) if part)
            found.append(
                {
                    "name": str(item.get("name") or ""),
                    "region": where,
                    "latitude": float(item["latitude"]),
                    "longitude": float(item["longitude"]),
                }
            )
        return found

    return _remember(("places", name.lower()), PLACE_SECONDS, fetch)


def _quote(symbol: str) -> dict[str, Any]:
    def fetch() -> dict[str, Any]:
        data = _get(CHART_URL.format(symbol=symbol), {"range": "1d", "interval": "5m"}, "Yahoo Finance")
        result = ((data or {}).get("chart") or {}).get("result") or []
        if not result:
            return {"symbol": symbol, "found": False}
        meta = result[0].get("meta") or {}
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        change = price - previous if price is not None and previous else None
        return {
            "symbol": symbol,
            "found": price is not None,
            "name": str(meta.get("shortName") or meta.get("longName") or symbol),
            "price": price,
            "change": change,
            "change_percent": change / previous * 100 if change is not None and previous else None,
            "currency": str(meta.get("currency") or ""),
            "points": [round(value, 4) for value in closes if value is not None][-80:],
        }

    return _remember(("stock", symbol), STOCK_SECONDS, fetch)


def stocks(symbols: list[str]) -> list[dict[str, Any]]:
    return [_quote(symbol) for symbol in symbols]


def _guide() -> str:
    from ctl.openwebui_ops import _guide as household

    with_assistant = {app.id for app in cached().apps if app.manifest.chat.assistant}
    return household(with_assistant)


def _system(name: str) -> str:
    guide = _guide()
    apps = (
        "\n\nThe household's installed apps (private to this server, reached over the household's "
        f"Tailscale network with the Mu3Lab account):\n{guide}"
        if guide
        else ""
    )
    return (
        "You are the Mu3Lab assistant, answering in a small box on the Home page of this household's "
        f"private server{f' for {name}' if name else ''}. Answer in a few short sentences or a short list; "
        "no headings or tables. You cannot see anyone's data in the apps here: when a question needs it "
        "(their photos, budget, files, events), say that the app's assistant in Chat can do it, and that "
        f"Continue in Chat carries the question there.{apps}"
    )


def ask(question: str, *, person: str, uid: str, paths: RuntimePaths | None = None) -> str:
    """A short answer from the household's chat model."""
    text = question.strip()
    if not text:
        raise WidgetError("Type a question first.")
    if len(text) > MAX_QUESTION:
        raise WidgetError(f"Questions here can be up to {MAX_QUESTION} characters; use Chat for longer ones.")
    try:
        proxy = by_capability("model_proxy")
    except (KeyError, ValueError):
        raise WidgetError("Chat is not set up on this server yet.") from None
    master = read_runtime_env((paths or RuntimePaths()).projects / proxy.id / ".env").get("LITELLM_MASTER_KEY", "")
    if not master:
        raise WidgetError("Chat is not set up on this server yet.")
    try:
        response = httpx.post(
            f"http://127.0.0.1:{proxy.manifest.service.local_port}/v1/chat/completions",
            headers={"Authorization": f"Bearer {master}"},
            json={
                "model": ASK_MODEL,
                "user": uid,
                "max_tokens": 600,
                "messages": [{"role": "system", "content": _system(person)}, {"role": "user", "content": text}],
            },
            timeout=ASK_SECONDS,
        )
    except httpx.TimeoutException:
        raise WidgetError("The chat model took too long. Try again, or Continue in Chat.") from None
    except httpx.HTTPError:
        raise WidgetError(f"{proxy.manifest.name} could not be reached; check that it is running in Apps.") from None
    if not response.is_success:
        raise WidgetError(
            f"The chat model did not answer (HTTP {response.status_code}). Check AI & voice in Apps, or "
            "Continue in Chat."
        )
    try:
        answer = str(response.json()["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError):
        answer = ""
    if not answer:
        raise WidgetError("The chat model gave an empty answer. Try asking another way.")
    return answer
