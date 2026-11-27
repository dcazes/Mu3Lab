"""What the Home widgets show that comes from outside Mu3Lab: weather, places, stock prices, answers.

The server fetches these, not the browser, so phones away from home need no extra access and
only coordinates and ticker symbols leave the house: never who is asking.

- Weather and place search: Open-Meteo (free, no account or key).
- Stock prices and the day's top movers: Yahoo Finance's public data (no account or key).
- Currency rates: the European Central Bank's daily reference rates, through Frankfurter.
- News feeds (RSS and Atom): the feeds the person adds, public addresses only.
- Ask: the household's own chat model through LiteLLM, told what every installed app is for,
  the same guide the main chat assistant has.

Answers are kept a few minutes, so a board open on several screens asks once.
"""

from __future__ import annotations

import re
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
MOVERS_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
RATES_URL = "https://api.frankfurter.dev/v1/latest"
RATES_SECONDS = 86400
FEED_SECONDS = 900
FEED_BYTES = 2_000_000
FEED_ITEMS = 30
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


# Open-Meteo forecasts up to 16 days; the widget shows as many of these as the person chooses.
FORECAST_DAYS = 14


def weather(latitude: float, longitude: float, units: str) -> dict[str, Any]:
    """Now and the next two weeks at one place, in the place's own time zone."""
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
                "forecast_days": FORECAST_DAYS,
                "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
                "wind_speed_unit": "mph" if fahrenheit else "kmh",
            },
            "The weather service",
        )
        if not data:
            raise WidgetError("The weather service has no forecast for this place.")
        current, daily = data.get("current") or {}, data.get("daily") or {}
        dates = daily.get("time") or []
        rain = daily.get("precipitation_probability_max") or [None] * len(dates)
        days = [
            {
                "date": date,
                "code": int(daily["weather_code"][index] or 0),
                "high": daily["temperature_2m_max"][index],
                "low": daily["temperature_2m_min"][index],
                "rain_chance": rain[index] if index < len(rain) else None,
            }
            for index, date in enumerate(dates)
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


# How far back each range looks, and the steps Yahoo gives for it (enough points for a small chart).
INTERVALS = {"1d": "5m", "5d": "30m", "1mo": "1d", "6mo": "1d", "ytd": "1d", "1y": "1d", "5y": "1wk"}
MAX_POINTS = 120


def _thinned(values: list[float]) -> list[float]:
    """At most ``MAX_POINTS`` evenly spread, always keeping the latest."""
    if len(values) <= MAX_POINTS:
        return values
    step = (len(values) - 1) / (MAX_POINTS - 1)
    return [values[round(index * step)] for index in range(MAX_POINTS)]


def _quote(symbol: str, span: str = "1d") -> dict[str, Any]:
    span = span if span in INTERVALS else "1d"

    def fetch() -> dict[str, Any]:
        data = _get(CHART_URL.format(symbol=symbol), {"range": span, "interval": INTERVALS[span]}, "Yahoo Finance")
        result = ((data or {}).get("chart") or {}).get("result") or []
        if not result:
            return {"symbol": symbol, "found": False}
        meta = result[0].get("meta") or {}
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
        points = [round(value, 4) for value in closes if value is not None]
        price = meta.get("regularMarketPrice")
        # Today: against yesterday's close. A longer range: against where it began.
        if span == "1d":
            previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        else:
            previous = points[0] if points else meta.get("chartPreviousClose")
        change = price - previous if price is not None and previous else None
        return {
            "symbol": symbol,
            "found": price is not None,
            "name": str(meta.get("shortName") or meta.get("longName") or symbol),
            "price": price,
            "change": change,
            "change_percent": change / previous * 100 if change is not None and previous else None,
            "currency": str(meta.get("currency") or ""),
            "points": _thinned(points),
        }

    # Today's line moves every minute; a longer range barely changes in a few minutes.
    return _remember(("stock", symbol, span), STOCK_SECONDS if span == "1d" else STOCK_SECONDS * 5, fetch)


def stocks(symbols: list[str], span: str = "1d") -> list[dict[str, Any]]:
    return [_quote(symbol, span) for symbol in symbols]


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


# ---------- The day's top movers ----------

MOVER_SCREENS = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}


def movers(which: str, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    """Yahoo Finance's day gainers, losers or most traded (or the person's own tickers): symbol,
    name, price and today's change, for a ticker that runs along the board."""
    if which == "mine":
        return [
            {key: quote.get(key) for key in ("symbol", "name", "price", "change_percent", "currency")}
            for quote in stocks(symbols or [])
            if quote.get("found")
        ]
    screen = MOVER_SCREENS.get(which, MOVER_SCREENS["gainers"])

    def fetch() -> list[dict[str, Any]]:
        data = _get(MOVERS_URL, {"scrIds": screen, "count": 25}, "Yahoo Finance")
        result = (((data or {}).get("finance") or {}).get("result") or [{}])[0] or {}
        found = []
        for quote in result.get("quotes") or []:
            price = quote.get("regularMarketPrice")
            if not quote.get("symbol") or price is None:
                continue
            found.append(
                {
                    "symbol": str(quote["symbol"])[:15],
                    "name": str(quote.get("shortName") or quote.get("longName") or quote["symbol"])[:60],
                    "price": price,
                    "change_percent": quote.get("regularMarketChangePercent"),
                    "currency": str(quote.get("currency") or ""),
                }
            )
        return found

    return _remember(("movers", screen), STOCK_SECONDS * 5, fetch)


# ---------- Currency rates ----------

CURRENCY = re.compile(r"^[A-Z]{3}$")


def rates(base: str) -> dict[str, Any]:
    """Today's reference rates from ``base`` to every other currency the ECB publishes (a day's cache)."""
    base = base.upper() if CURRENCY.match(base.upper()) else "USD"

    def fetch() -> dict[str, Any]:
        data = _get(RATES_URL, {"base": base}, "The currency rates service")
        if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
            raise WidgetError("The currency rates service did not give today's rates. Try again later.")
        return {
            "base": base,
            "date": str(data.get("date") or ""),
            "rates": {str(code): float(value) for code, value in data["rates"].items() if CURRENCY.match(str(code))},
        }

    return _remember(("rates", base), RATES_SECONDS, fetch)


# ---------- News feeds ----------


def _public_fetch(url: str) -> bytes:
    """A public web address's body, at most ``FEED_BYTES``: never this server's, the home network's or
    the tailnet's (checked before asking, after every redirect, and against who actually answered)."""
    import ipaddress

    from ctl.home_search import SearchError, public_address

    for _ in range(4):
        try:
            url = public_address(url)
        except SearchError as exc:
            raise WidgetError(str(exc)) from None
        try:
            with httpx.stream("GET", url, headers=AGENT, timeout=10, follow_redirects=False) as response:
                stream = response.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream is not None else None
                if peer and not ipaddress.ip_address(str(peer[0]).split("%")[0]).is_global:
                    raise WidgetError("Only public feeds can be read here.")
                if response.is_redirect and response.headers.get("location"):
                    url = str(response.url.join(response.headers["location"]))
                    continue
                if not response.is_success:
                    raise WidgetError(f"The feed did not answer (HTTP {response.status_code}).")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > FEED_BYTES:
                        raise WidgetError("That feed is too large to show here.")
                return body
        except httpx.HTTPError:
            raise WidgetError("The feed could not be reached.") from None
    raise WidgetError("The feed sent Mu3Lab round too many redirects.")


def _plain(text: str | None, limit: int) -> str:
    import html
    import re

    cleaned = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return " ".join(html.unescape(cleaned).split())[:limit]


def _feed_items(body: bytes) -> tuple[str, list[dict[str, Any]]]:
    """RSS 2.0, RSS 1.0 or Atom as (feed title, items). Entities are refused outright, so no feed can
    blow up the parser or point it at a file."""
    import xml.etree.ElementTree as ElementTree
    from datetime import datetime
    from email.utils import parsedate_to_datetime

    if b"<!ENTITY" in body or b"<!entity" in body:
        raise WidgetError("That feed could not be read.")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise WidgetError("That address is not a news feed.") from None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def child(node: Any, *names: str) -> Any:
        return next((item for item in node if local(item.tag) in names), None)

    def when(text: str | None) -> float:
        if not text:
            return 0.0
        try:
            return parsedate_to_datetime(text).timestamp()
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    items: list[dict[str, Any]] = []
    channel = child(root, "channel")
    title = ""
    if local(root.tag) == "feed":  # Atom
        title = _plain(getattr(child(root, "title"), "text", ""), 80)
        for entry in (item for item in root if local(item.tag) == "entry"):
            link = next(
                (
                    item.get("href", "")
                    for item in entry
                    if local(item.tag) == "link" and item.get("rel", "alternate") == "alternate"
                ),
                "",
            )
            summary = child(entry, "summary", "content")
            items.append(
                {
                    "title": _plain(getattr(child(entry, "title"), "text", ""), 200),
                    "url": link,
                    "summary": _plain(getattr(summary, "text", ""), 300),
                    "published": when(getattr(child(entry, "published", "updated"), "text", None)),
                }
            )
    else:  # RSS 2.0 (items in the channel) or RSS 1.0 (items beside it)
        title = _plain(getattr(child(channel, "title") if channel is not None else None, "text", ""), 80)
        holders = [channel, root] if channel is not None else [root]
        for holder in holders:
            for entry in (item for item in holder if local(item.tag) == "item"):
                items.append(
                    {
                        "title": _plain(getattr(child(entry, "title"), "text", ""), 200),
                        "url": (getattr(child(entry, "link"), "text", "") or "").strip(),
                        "summary": _plain(getattr(child(entry, "description", "encoded"), "text", ""), 300),
                        "published": when(getattr(child(entry, "pubdate", "date"), "text", None)),
                    }
                )
    clean = [item for item in items if item["title"] and str(item["url"]).lower().startswith(("https://", "http://"))]
    return title, clean[:FEED_ITEMS]


def feed(url: str) -> dict[str, Any]:
    """One news feed's newest items: title, link, a short summary and when (kept 15 minutes)."""

    def fetch() -> dict[str, Any]:
        title, items = _feed_items(_public_fetch(url))
        return {"url": url, "title": title, "items": items}

    return _remember(("feed", url), FEED_SECONDS, fetch)
