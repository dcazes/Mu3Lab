"""Regression tests for the review's gateway authorization and transport findings."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ctl import mcp_gateway
from tests.test_mcp_gateway import FakeConnector, _app, _review, gateway


class WriteContainmentTests(unittest.TestCase):
    def test_enabled_writes_are_unavailable_on_every_path(self):
        app = _app(rename={"enabled": True, "core": True})
        connector = FakeConnector(["search", "details", "rename"])
        with patch.object(gateway.CONNECTORS, "get", return_value=connector):
            for name, arguments in (
                ("rename", {"id": 1}),
                ("change_with_tool", {"tool": "rename", "arguments": {"id": 1}}),
                ("use_tool", {"tool": "rename", "arguments": {"id": 1}}),
            ):
                with self.subTest(path=name), self.assertRaises(gateway.GatewayError):
                    gateway.handle_call("demo", app, name, arguments)
            names = {tool["name"] for tool in gateway.list_tools("demo", app)}
            self.assertNotIn("rename", names)
            self.assertNotIn("change_with_tool", names)
            found = gateway.find_tools("demo", app, {"category": "search"})["tools"]
            write = next(tool for tool in found if tool["tool"] == "rename")
            self.assertEqual(write["state"], "off")
            self.assertIsNone(write["run_with"])
            self.assertIn("approval", write["note"])
        self.assertEqual(connector.calls, [])


class PolicyRevocationTests(unittest.TestCase):
    def test_deletion_and_invalid_json_revoke_cached_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            store = gateway.PolicyStore(str(path))
            original = {"version": 1, "apps": {"demo": _app()}}
            path.write_text(json.dumps(original))
            self.assertEqual(store.get(), original)
            path.unlink()
            with self.assertRaises((OSError, ValueError)):
                store.get()
            path.write_text("{")
            with self.assertRaises((OSError, ValueError)):
                store.get()

    def test_same_timestamp_replacement_is_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"version": 1, "apps": {"demo": _app()}}))
            stamp = path.stat().st_mtime_ns
            store = gateway.PolicyStore(str(path))
            self.assertIn("demo", store.get()["apps"])
            path.write_text(json.dumps({"version": 1, "apps": {}}))
            os.utime(path, ns=(stamp, stamp))
            self.assertEqual(store.get()["apps"], {})

    def test_unreadable_or_invalid_tool_policy_is_not_cached_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"version": 1, "apps": {"demo": _app()}}))
            store = gateway.PolicyStore(str(path))
            store.get()
            with patch("builtins.open", side_effect=PermissionError), self.assertRaises(OSError):
                store.get()
            app = _app(search={"access": "unknown"})
            path.write_text(json.dumps({"version": 1, "apps": {"demo": app}}))
            with self.assertRaises(ValueError):
                store.get()


class TransportSafetyTests(unittest.TestCase):
    def test_unknown_call_semantics_never_retry_after_dispatch(self):
        for error in (URLError("lost reply"), OSError("lost reply"), HTTPError("url", 404, "lost session", {}, None)):
            connector = gateway.Connector({"url": "http://demo/mcp"})
            connector.session = "existing"
            with patch.object(connector, "_post", side_effect=error) as post:
                with self.subTest(error=type(error).__name__), self.assertRaisesRegex(gateway.GatewayError, "unknown"):
                    connector.request("tools/call", {"name": "write", "arguments": {}})
                self.assertEqual(post.call_count, 1)

    def test_explicit_read_retry_reinitializes_before_second_dispatch(self):
        connector = gateway.Connector({"url": "http://demo/mcp"})
        connector.session = "existing"
        events = []

        def initialize():
            events.append("initialize")
            connector.session = "fresh"

        def post(body):
            events.append(body["method"])
            if len(events) == 1:
                raise URLError("lost reply")
            return {"result": {"content": []}}, "fresh"

        with (
            patch.object(connector, "_initialize", side_effect=initialize),
            patch.object(connector, "_post", side_effect=post),
        ):
            connector.request("tools/call", {"name": "read"}, retry_safe=True)
        self.assertEqual(events, ["tools/call", "initialize", "tools/call"])


class PolicyPublicationTests(unittest.TestCase):
    def test_publication_is_private_and_builds_are_serialized(self):
        first_build = threading.Event()
        release = threading.Event()
        second_build = threading.Event()
        built = []

        def build():
            number = len(built) + 1
            built.append(number)
            if number == 1:
                first_build.set()
                if not release.wait(timeout=5):
                    raise AssertionError("first build was not released")
            else:
                second_build.set()
            return {"version": 1, "apps": {}, "test_snapshot": number}

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with (
                patch.object(mcp_gateway, "project", return_value=project),
                patch.object(mcp_gateway, "build_policy", side_effect=build),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(mcp_gateway.write_policy)
                self.assertTrue(first_build.wait(timeout=5))
                second = pool.submit(mcp_gateway.write_policy)
                try:
                    self.assertFalse(second_build.wait(timeout=0.1))
                finally:
                    release.set()
                first.result(timeout=5)
                second.result(timeout=5)
            path = project / "policy" / "policy.json"
            self.assertEqual(json.loads(path.read_text())["test_snapshot"], 2)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class GatewayHttpBoundaryTests(unittest.TestCase):
    def test_valid_token_cannot_bypass_write_gate_and_removed_policy_returns_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            app = _app(rename={"enabled": True, "core": True})
            app["token_sha256"] = hashlib.sha256(b"test-token").hexdigest()
            path.write_text(json.dumps({"version": 1, "apps": {"demo": app}}))
            connector = FakeConnector(["search", "details", "rename"])
            with (
                patch.object(gateway, "POLICY", gateway.PolicyStore(str(path))),
                patch.object(gateway, "CALL_LOG", str(Path(tmp) / "calls.jsonl")),
                patch.object(gateway.CONNECTORS, "get", return_value=connector),
            ):
                server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}/apps/demo/mcp"

                    def call(name, arguments):
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        }
                        request = Request(
                            url,
                            data=json.dumps(payload).encode(),
                            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
                        )
                        with urlopen(request, timeout=5) as response:
                            return json.loads(response.read())["result"]

                    for name, arguments in (("rename", {}), ("change_with_tool", {"tool": "rename"})):
                        self.assertTrue(call(name, arguments)["isError"])
                    self.assertEqual(connector.calls, [])
                    self.assertFalse(call("search", {})["isError"])
                    self.assertEqual(len(connector.calls), 1)
                    path.unlink()
                    with self.assertRaises(HTTPError) as raised:
                        call("search", {})
                    self.assertEqual(raised.exception.code, 503)
                    raised.exception.close()
                    self.assertEqual(len(connector.calls), 1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


class GatewayDeploymentTests(unittest.TestCase):
    def test_published_policy_disables_writes_even_for_a_legacy_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = mcp_gateway.load_review("demo", _review(tmp).name, Path(tmp))
            server = type("Server", (), {"id": "demo", "service_id": "demo", "endpoint": "http://demo/mcp"})()
            states = {"categories": {"search": True, "trash": True}, "tools": dict.fromkeys(review.tools, True)}
            with (
                patch.object(mcp_gateway, "review_for", return_value=review),
                patch.object(mcp_gateway, "tool_states", return_value=states),
                patch.object(mcp_gateway, "app_token", return_value="test-token"),
                patch.object(mcp_gateway, "read_runtime_env", return_value={}),
            ):
                policy = mcp_gateway.app_policy(server, "Demo")
            self.assertTrue(policy["tools"]["search"]["enabled"])
            self.assertFalse(policy["tools"]["rename"]["enabled"])
            self.assertFalse(policy["tools"]["delete"]["enabled"])

    def test_development_gateway_builds_changed_source_and_caches_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "Dockerfile").write_text("FROM python\n")
            source = target / "gateway.py"
            source.write_text("version = 1\n")
            with (
                patch.object(mcp_gateway.platform_releases, "exact_tag", return_value=""),
                patch.object(mcp_gateway.actions, "docker_cmd", return_value=(0, "built")) as build,
            ):
                self.assertEqual(mcp_gateway._build_gateway(target, lambda _: None)[0], 0)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(mcp_gateway._build_gateway(target, lambda _: None)[0], 0)
                self.assertEqual(build.call_count, 1)
                source.write_text("version = 2\n")
                mcp_gateway._build_gateway(target, lambda _: None)
                self.assertEqual(build.call_count, 2)

    def test_release_gateway_uses_verified_image_without_local_build(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(mcp_gateway.platform_releases, "exact_tag", return_value="v1.0.0"),
            patch.object(mcp_gateway.actions, "docker_cmd") as build,
        ):
            self.assertEqual(mcp_gateway._build_gateway(Path(tmp), lambda _: None)[0], 0)
            build.assert_not_called()

    def test_failed_build_is_not_cached_and_refresh_does_not_start_stale_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "Dockerfile").write_text("FROM python\n")
            (target / "gateway.py").write_text("version = 1\n")
            with (
                patch.object(mcp_gateway.platform_releases, "exact_tag", return_value=""),
                patch.object(mcp_gateway.actions, "docker_cmd", side_effect=[(1, "failed"), (0, "built")]) as build,
            ):
                self.assertEqual(mcp_gateway._build_gateway(target, lambda _: None)[0], 1)
                self.assertFalse((target / ".build.sha256").exists())
                self.assertEqual(mcp_gateway._build_gateway(target, lambda _: None)[0], 0)
                self.assertEqual(build.call_count, 2)
            with (
                patch.object(mcp_gateway, "write_policy"),
                patch.object(mcp_gateway, "_materialize", return_value=target),
                patch.object(mcp_gateway, "_build_gateway", return_value=(1, "failed")),
                patch.object(mcp_gateway.actions, "compose_up") as up,
            ):
                self.assertFalse(mcp_gateway.refresh(lambda _: None)[0])
                up.assert_not_called()
