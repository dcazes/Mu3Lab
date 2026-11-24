"""The Search widget: pages read as plain text, Firecrawl never sent to a private address, and the
right half answering each kind of search."""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from ctl import home_search


def resolves_to(address: str):
    return patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", (address, 443))])


class PlainTextTests(unittest.TestCase):
    def test_markdown_reads_as_text(self):
        text = home_search.plain(
            "# Title\n\n![logo](https://x/y.png) Read **the** [guide](https://x/g).\n\n\n\n- one\n- two\n> quoted"
        )
        self.assertEqual(text, "Title\n\nRead the guide.\n\n• one\n• two\nquoted")


class AddressTests(unittest.TestCase):
    def test_public_pages_only(self):
        with resolves_to("93.184.215.14"):
            self.assertEqual(home_search.public_address("example.com/a"), "https://example.com/a")
        for private in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "100.101.102.103", "169.254.169.254", "::1"):
            with self.subTest(private=private), resolves_to(private), self.assertRaises(home_search.SearchError):
                home_search.public_address("http://sneaky.example")
        for bad in ("ftp://example.com", "https://user:pw@example.com"):
            with self.subTest(bad=bad), self.assertRaises(home_search.SearchError):
                home_search.public_address(bad)


class ModeTests(unittest.TestCase):
    def test_each_search_goes_to_its_half(self):
        with (
            patch.object(home_search, "web", return_value={"mode": "web"}) as web,
            patch.object(home_search, "read_page", return_value={"mode": "page"}) as page,
            patch.object(home_search, "deep", return_value={"mode": "deep"}) as deep,
        ):
            self.assertEqual(home_search.search("weather tomorrow", "web")["mode"], "web")
            self.assertEqual(home_search.search("https://example.com/news", "fetch")["mode"], "page")
            self.assertEqual(home_search.search("example.com", "fetch")["mode"], "page")
            self.assertEqual(home_search.search("best pizza dough", "fetch")["mode"], "deep")
        web.assert_called_once_with("weather tomorrow")
        self.assertEqual(page.call_count, 2)
        deep.assert_called_once_with("best pizza dough")
        with self.assertRaises(home_search.SearchError):
            home_search.search("   ", "web")

    def test_deep_search_shows_each_pages_own_words_or_the_summary(self):
        found = {
            "results": [
                {"title": "A", "url": "https://a.example/", "content": "summary a"},
                {"title": "B", "url": "https://b.example/", "content": "summary b"},
            ]
        }

        def scrape(url):
            if "b.example" in url:
                raise home_search.SearchError("no")
            return {"title": "A", "url": url, "text": "The page itself."}

        with (
            patch.object(home_search, "_searxng", return_value=found),
            patch.object(home_search, "_scrape", side_effect=scrape),
            resolves_to("93.184.215.14"),
        ):
            results = home_search.deep("q")["results"]
        self.assertEqual((results[0]["snippet"], results[0].get("read")), ("The page itself.", True))
        self.assertEqual((results[1]["snippet"], results[1].get("read")), ("summary b", None))


if __name__ == "__main__":
    unittest.main()
