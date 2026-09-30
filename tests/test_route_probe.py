"""A private route is "verified" only after a real request through it succeeds."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ctl import service_state


class _Server:
    def __init__(self):
        self.status = 200
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.status)
                if outer.status in {301, 302}:
                    self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"


class RouteProbeTests(unittest.TestCase):
    def setUp(self):
        self.server = _Server()
        self.addCleanup(self.server.httpd.shutdown)
        service_state._route_probes.clear()

    def check(self, status: int) -> bool:
        service_state._route_probes.clear()
        self.server.status = status
        return service_state.route_answers(self.server.url)

    def test_a_sign_in_redirect_or_login_page_counts_as_answering(self):
        for status in (200, 302, 401, 403):
            with self.subTest(status=status):
                self.assertTrue(self.check(status))

    def test_wrong_destination_and_server_errors_do_not(self):
        for status in (404, 500, 502):
            with self.subTest(status=status):
                self.assertFalse(self.check(status))

    def test_an_unreachable_address_does_not(self):
        self.assertFalse(service_state.route_answers("http://127.0.0.1:9/"))

    def test_results_are_reused_for_a_minute(self):
        clock = [1000.0]
        service_state.route_answers(self.server.url, now=lambda: clock[0])
        service_state.route_answers(self.server.url, now=lambda: clock[0] + 30)
        self.assertEqual(self.server.hits, 1)
        service_state.route_answers(self.server.url, now=lambda: clock[0] + 61)
        self.assertEqual(self.server.hits, 2)


if __name__ == "__main__":
    unittest.main()
