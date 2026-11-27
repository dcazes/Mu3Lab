"""The Search widget: the household's private web search (SearXNG), or Firecrawl, which reads pages.

* Web: SearXNG's results (title, address, its summary), and an instant answer when it has one.
* Firecrawl, given a web address: that page as clean text (no menus or ads), to read on Home.
* Firecrawl, given words: a deep search. SearXNG finds the pages (so the search stays private, as
  Firecrawl on its own would ask other search engines) and Firecrawl reads the top few, so each
  result shows the page's own opening text rather than a search engine's summary.

Firecrawl fetches pages from this server, so it is only ever given public web addresses: an address
that leads to this server, the home network or the tailnet is refused, so nobody can read a private
app's pages through it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

import httpx

from ctl.manifest.catalog import App
from ctl.platform_apps import by_capability

MAX_QUERY = 500
RESULTS = 8
# How many of a deep search's pages Firecrawl reads, and how much of each is shown.
DEEP_PAGES = 3
DEEP_CHARS = 500
PAGE_CHARS = 12_000
SEARCH_SECONDS = 15
READ_SECONDS = 45
ADDRESS = re.compile(r"^(https?://)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?(/\S*)?$")


class SearchError(RuntimeError):
    """Why there are no results; the message is for the person."""


def _app(capability: str) -> App:
    return by_capability(capability)


def _installed(app: App) -> bool:
    from ctl import chat_ops

    return chat_ops.installed(app.id)


def available() -> dict[str, Any]:
    """Which halves of the widget can work (each app installed), and the apps' names for its switch."""
    found: dict[str, Any] = {}
    for mode, capability in (("web", "web_search"), ("fetch", "web_fetch")):
        try:
            app = _app(capability)
        except ValueError:
            found[mode], found[f"{mode}_name"] = False, ""
            continue
        found[mode], found[f"{mode}_name"] = _installed(app), app.manifest.name
    return found


def _base(app: App) -> str:
    return f"http://127.0.0.1:{app.manifest.service.local_port}"


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _searxng(query: str) -> dict[str, Any]:
    app = _app("web_search")
    if not _installed(app):
        raise SearchError(f"Install {app.manifest.name} from Apps to search the web here.")
    try:
        reply = httpx.get(
            _base(app) + "/search",
            params={"q": query, "format": "json", "safesearch": "1"},
            timeout=SEARCH_SECONDS,
        )
        reply.raise_for_status()
        data = reply.json()
    except httpx.TimeoutException:
        raise SearchError(f"{app.manifest.name} took too long. Try again.") from None
    except (httpx.HTTPError, ValueError):
        raise SearchError(f"{app.manifest.name} did not answer; check that it is running in Apps.") from None
    return data if isinstance(data, dict) else {}


def _answer(value: Any) -> str:
    # SearXNG gives instant answers as text or, in newer releases, as {"answer": ..., "url": ...}.
    return _clean(value.get("answer") if isinstance(value, dict) else value, 400)


def _results(data: dict[str, Any]) -> list[dict[str, str]]:
    results = []
    for result in data.get("results") or []:
        url = str(result.get("url") or "")
        if not url.startswith(("https://", "http://")) or len(url) > 2000:
            continue
        results.append(
            {
                "title": _clean(result.get("title"), 200) or url,
                "url": url,
                "snippet": _clean(result.get("content"), 400),
            }
        )
        if len(results) == RESULTS:
            break
    return results


def web(query: str) -> dict[str, Any]:
    data = _searxng(query)
    answer = next((text for text in map(_answer, data.get("answers") or []) if text), "")
    return {"mode": "web", "answer": answer, "results": _results(data), "page": None}


def public_address(text: str) -> str:
    """``text`` as a public web address, or ``SearchError`` (a private or local address, or none)."""
    url = text if text.lower().startswith(("http://", "https://")) else "https://" + text
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise SearchError("That is not a web address that can be read here.")
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        raise SearchError(f"{host} could not be found.") from None
    for address in found:
        if not ipaddress.ip_address(str(address).split("%")[0]).is_global:
            raise SearchError("Only public web pages can be read here, not this server's or the home network's.")
    return url


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKS = re.compile(r"^[ \t]{0,3}(#{1,6}\s+|>\s?|[-*+]\s+(?=\S))|(\*\*|__|`{1,3})", re.MULTILINE)


def plain(markdown: str) -> str:
    """A page's Markdown as text to read: no pictures, links as their words, no formatting marks."""
    text = _LINK.sub(r"\1", _IMAGE.sub("", markdown))
    text = _MARKS.sub(lambda match: "• " if match.group(1) and match.group(1).strip()[:1] in "-*+" else "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _scrape(url: str) -> dict[str, str]:
    app = _app("web_fetch")
    if not _installed(app):
        raise SearchError(f"Install {app.manifest.name} from Apps to read pages here.")
    try:
        reply = httpx.post(
            _base(app) + "/v1/scrape",
            # Firecrawl here takes any key (no accounts); it still wants one.
            headers={"Authorization": "Bearer mu3lab"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": READ_SECONDS * 1000},
            timeout=READ_SECONDS + 10,
        )
        data = reply.json()
    except httpx.TimeoutException:
        raise SearchError(f"{app.manifest.name} took too long to read that page.") from None
    except (httpx.HTTPError, ValueError):
        raise SearchError(f"{app.manifest.name} did not answer; check that it is running in Apps.") from None
    page = (data or {}).get("data") if isinstance(data, dict) else None
    if not reply.is_success or not isinstance(page, dict):
        raise SearchError(f"{app.manifest.name} could not read that page.")
    meta = page.get("metadata") or {}
    return {
        "title": _clean(meta.get("title"), 200),
        "url": str(meta.get("sourceURL") or meta.get("url") or url),
        "text": plain(str(page.get("markdown") or "")),
    }


def read_page(text: str) -> dict[str, Any]:
    page = _scrape(public_address(text))
    if not page["text"]:
        raise SearchError("That page has no text to show.")
    page["text"] = page["text"][:PAGE_CHARS]
    page["title"] = page["title"] or page["url"]
    return {"mode": "page", "answer": "", "results": [], "page": page}


def deep(query: str) -> dict[str, Any]:
    results = _results(_searxng(query))

    def opening(result: dict[str, str]) -> dict[str, Any]:
        try:
            page = _scrape(public_address(result["url"]))
        except SearchError:
            return result  # the search engine's summary stands in
        text = " ".join(page["text"].split())
        return {**result, "snippet": text[:DEEP_CHARS] or result["snippet"], "read": True}

    with ThreadPoolExecutor(max_workers=DEEP_PAGES) as pool:
        read = list(pool.map(opening, results[:DEEP_PAGES]))
    return {"mode": "deep", "answer": "", "results": read + results[DEEP_PAGES:], "page": None}


def search(query: str, mode: str) -> dict[str, Any]:
    text = query.strip()
    if not text:
        raise SearchError("Type something to search for.")
    if len(text) > MAX_QUERY:
        raise SearchError(f"Searches can be up to {MAX_QUERY} characters.")
    if mode == "fetch":
        return read_page(text) if ADDRESS.match(text) else deep(text)
    return web(text)
