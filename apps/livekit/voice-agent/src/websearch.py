"""Private web search through the household's SearXNG app (Mu3Lab's ``web_search`` capability).

Searches leave this server without an account or key. The assistant gets the top results as short
plain text it can answer from; links are left out, since they are not read aloud.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger("mu3lab-voice-agent")

SEARCH_SECONDS = 10
RESULTS = 3
RESULT_CHARS = 300


def _answer(value: Any) -> str:
    # SearXNG gives instant answers as text or, in newer releases, as {"answer": ..., "url": ...}.
    return str(value.get("answer") or "") if isinstance(value, dict) else str(value)


async def results(http: aiohttp.ClientSession, base_url: str, query: str) -> list[str]:
    """The top results as "title: summary" lines, an instant answer first; raises when the search fails."""
    async with http.get(
        f"{base_url}/search",
        params={"q": query, "format": "json", "safesearch": "1"},
        timeout=aiohttp.ClientTimeout(total=SEARCH_SECONDS),
    ) as reply:
        reply.raise_for_status()
        data = await reply.json()
    lines = [text for text in (_answer(value).strip() for value in data.get("answers") or []) if text][:1]
    for result in (data.get("results") or [])[:RESULTS]:
        title = str(result.get("title") or "").strip()
        content = " ".join(str(result.get("content") or "").split())[:RESULT_CHARS]
        lines.append(f"- {title}: {content}" if content else f"- {title}")
    return lines


async def search(http: aiohttp.ClientSession, base_url: str, query: str) -> str:
    """What the turn-based assistant reads: the top results, or why there are none."""
    try:
        lines = await results(http, base_url, query)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("Web search failed: %s", exc)
        return "The search did not work just now. Say so briefly."
    return "\n".join(lines) if lines else "Nothing was found."
