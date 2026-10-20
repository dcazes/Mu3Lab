"""Chat connectors for Audiobookshelf, Navidrome and RomM: Mu3Lab's adapter and how each gets its credential."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ctl import mcp_review
from ctl.mcp_catalog import load as load_connectors
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
MODES = {"audiobookshelf-adapter": "audiobookshelf", "navidrome-adapter": "navidrome", "romm-adapter": "romm"}


def adapter(mode: str):
    """The shared adapter in one mode, kept from listening."""
    with patch.dict("os.environ", {"MCP_APP": mode}), patch("http.server.ThreadingHTTPServer"):
        spec = importlib.util.spec_from_file_location(f"mu3lab_{mode}_adapter", ROOT / "platform/mcp-adapter/server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def replies(*bodies):
    """A stand-in for the adapter's ``_request`` that records each route and answers in turn."""
    sent: list[str] = []
    queue = list(bodies)

    def fake(route, *_args):
        sent.append(route)
        return json.dumps(queue.pop(0)).encode(), 200

    return fake, sent


class CatalogTests(unittest.TestCase):
    def test_each_review_names_exactly_the_adapters_tools(self):
        connectors = {connector.id: connector for connector in load_connectors(load_registry())}
        for connector_id, mode in MODES.items():
            with self.subTest(connector=connector_id):
                server = connectors[connector_id]
                self.assertTrue(server.preferred)
                review = mcp_review.load(server.id, server.review)
                self.assertEqual(set(review.tools), {item["name"] for item in adapter(mode).TOOLS})

    def test_the_owners_listening_starts_switched_off(self):
        from ctl import contextforge_ops

        server = next(c for c in load_connectors(load_registry()) if c.id == "audiobookshelf-adapter")
        self.assertEqual(
            contextforge_ops.starting_tools(server), {"list_libraries", "search_library", "list_recent_items"}
        )

    def test_a_connector_has_one_way_to_get_its_credential(self):
        from pydantic import ValidationError

        from ctl.manifest.models import Connector

        raw = {
            "id": "x",
            "name": "X",
            "provenance": "mu3lab-adapter",
            "repository": "https://example.com/x",
            "revision": "1",
            "endpoint": "http://x:8080/mcp",
            "local_health": "http://127.0.0.1:1/health",
            "provision_hook": True,
        }
        self.assertTrue(Connector.model_validate(raw).auto_provision)
        script = {"service": "x", "script": "p.py", "interpreter": ["python", "-"]}
        with self.assertRaises(ValidationError):
            Connector.model_validate(raw | {"provision": script})


class NavidromeCredentialTests(unittest.TestCase):
    def test_the_connector_uses_mu3labs_own_navidrome_administrator(self):
        from ctl import mcp_credentials

        with tempfile.TemporaryDirectory() as temp:
            paths = RuntimePaths(Path(temp))
            (paths.projects / "navidrome").mkdir(parents=True)
            (paths.projects / "navidrome" / ".env").write_text("NAVIDROME_ADMIN_PASSWORD=service-secret\n")
            server = next(c for c in load_connectors(load_registry()) if c.id == "navidrome-adapter")
            with (
                patch("ctl.mcp_credentials.missing_credentials", return_value=["password"]),
                patch("ctl.mcp_credentials.RuntimePaths", return_value=paths),
                patch("ctl.mcp_credentials.mcp_config.write") as write,
            ):
                ok, _detail = mcp_credentials.ensure(server, lambda _line: None)
        self.assertTrue(ok)
        self.assertEqual(write.call_args.args[1], {"password": "service-secret"})

    def test_a_missing_password_is_reported_not_written(self):
        from ctl import mcp_credentials

        with tempfile.TemporaryDirectory() as temp:
            server = next(c for c in load_connectors(load_registry()) if c.id == "navidrome-adapter")
            with (
                patch("ctl.mcp_credentials.missing_credentials", return_value=["password"]),
                patch("ctl.mcp_credentials.RuntimePaths", return_value=RuntimePaths(Path(temp))),
                patch("ctl.mcp_credentials.mcp_config.write") as write,
            ):
                ok, detail = mcp_credentials.ensure(server, lambda _line: None)
        self.assertFalse(ok)
        self.assertIn("reinstall Navidrome", detail)
        write.assert_not_called()


class AdapterTest(unittest.TestCase):
    """The adapter reads its credential at each call, so the settings stay in place for the whole test."""

    mode = ""
    env: ClassVar[dict[str, str]] = {}

    def setUp(self):
        patcher = patch.dict("os.environ", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.adapter = adapter(self.mode)


class NavidromeAdapterTests(AdapterTest):
    mode = "navidrome"
    env: ClassVar[dict[str, str]] = {"NAVIDROME_USERNAME": "mu3lab-service", "NAVIDROME_PASSWORD": "pw"}

    def test_each_call_is_signed_without_sending_the_password(self):
        fake, sent = replies({"subsonic-response": {"status": "ok", "playlists": {"playlist": []}}})
        with patch.object(self.adapter, "_request", side_effect=fake):
            self.assertEqual(self.adapter._navidrome("list_playlists", {}), [])
        query = parse_qs(urlsplit(sent[0]).query)
        self.assertEqual(urlsplit(sent[0]).path, "/rest/getPlaylists")
        self.assertEqual(query["u"], ["mu3lab-service"])
        self.assertEqual(query["t"], [hashlib.md5(("pw" + query["s"][0]).encode()).hexdigest()])
        self.assertNotIn("pw", sent[0].split("&t=")[0])

    def test_a_refusal_becomes_an_error(self):
        fake, _sent = replies({"subsonic-response": {"status": "failed", "error": {"message": "Wrong username"}}})
        with patch.object(self.adapter, "_request", side_effect=fake), self.assertRaisesRegex(ValueError, "Wrong"):
            self.adapter._navidrome("list_playlists", {})

    def test_a_new_playlist_is_made_public(self):
        created = {"subsonic-response": {"status": "ok", "playlist": {"id": "p1", "name": "Road trip"}}}
        fake, sent = replies(created, {"subsonic-response": {"status": "ok"}})
        with patch.object(self.adapter, "_request", side_effect=fake):
            result = self.adapter._navidrome("create_playlist", {"name": "Road trip", "song_ids": ["s1", "s2"]})
        self.assertEqual(result, {"id": "p1", "name": "Road trip", "public": True})
        self.assertEqual(parse_qs(urlsplit(sent[0]).query)["songId"], ["s1", "s2"])
        self.assertEqual(parse_qs(urlsplit(sent[1]).query)["public"], ["true"])

    def test_song_ids_must_be_a_list(self):
        with self.assertRaises(ValueError):
            self.adapter._navidrome("create_playlist", {"name": "x", "song_ids": "s1"})


class RommAdapterTests(AdapterTest):
    mode = "romm"
    env: ClassVar[dict[str, str]] = {"ROMM_API_TOKEN": "rmm_x"}

    def test_search_asks_for_one_page_without_the_index(self):
        rom = {"id": 7, "name": "Chrono Trigger", "platform_display_name": "SNES", "metadatum": {"genres": ["RPG"]}}
        fake, sent = replies({"total": 1, "items": [rom]})
        with patch.object(self.adapter, "_request", side_effect=fake):
            found = self.adapter._romm("search_games", {"query": " chrono ", "platform_id": 3, "limit": 5})
        query = parse_qs(urlsplit(sent[0]).query)
        self.assertEqual(query["search_term"], ["chrono"])
        self.assertEqual(query["platform_ids"], ["3"])
        self.assertEqual(query["with_rom_id_index"], ["false"])
        self.assertEqual(found["games"][0]["system"], "SNES")

    def test_ids_must_be_numbers(self):
        for args in ({"query": "x", "platform_id": "3"}, {"query": "x", "platform_id": True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.adapter._romm("search_games", args)
        with self.assertRaises(ValueError):
            self.adapter._romm("get_game", {"game_id": "7"})

    def test_the_token_is_sent_as_a_bearer_token(self):
        self.assertEqual(self.adapter._auth(), "Bearer rmm_x")


class AudiobookshelfAdapterTests(AdapterTest):
    mode = "audiobookshelf"
    env: ClassVar[dict[str, str]] = {"ABS_API_KEY": "key"}

    def test_listening_stats_keep_the_ten_most_listened(self):
        items = {f"i{n}": {"timeListening": n, "mediaMetadata": {"title": f"Book {n}"}} for n in range(12)}
        fake, _sent = replies({"totalTime": 66, "today": 5, "items": items})
        with patch.object(self.adapter, "_request", side_effect=fake):
            stats = self.adapter._audiobookshelf("listening_stats", {})
        self.assertEqual(len(stats["most_listened"]), 10)
        self.assertEqual(stats["most_listened"][0], {"title": "Book 11", "seconds": 11})

    def test_search_returns_titles_not_whole_records(self):
        book = {"id": "li1", "mediaType": "book", "media": {"metadata": {"title": "Dune", "authorName": "Herbert"}}}
        fake, sent = replies({"book": [{"libraryItem": book}], "authors": [{"name": "Herbert"}], "series": "odd"})
        with patch.object(self.adapter, "_request", side_effect=fake):
            found = self.adapter._audiobookshelf("search_library", {"library_id": "lib 1", "query": "dune"})
        self.assertTrue(sent[0].startswith("/api/libraries/lib%201/search?"))
        self.assertEqual(found["books"][0]["title"], "Dune")
        self.assertEqual((found["authors"], found["series"]), (["Herbert"], []))


if __name__ == "__main__":
    unittest.main()
