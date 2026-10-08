"""R06: the gateway validates every request and stays bounded under abuse or slowness."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ctl import mcp_gateway
from gateway_authority import Authority, schema_digest
from tests.test_gateway_security import publish_fixture
from tests.test_mcp_gateway import FakeConnector, _app, _review, gateway


class EnvelopeTests(unittest.TestCase):
    def rejects(self, raw, code, status=None):
        with self.assertRaises(gateway.RpcError) as raised:
            gateway.parse_message(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
        self.assertEqual(raised.exception.code, code)
        if status:
            self.assertEqual(raised.exception.status, status)

    def test_malformed_envelopes_get_deterministic_errors(self):
        valid = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.rejects(b"{not json", gateway.PARSE_ERROR, 400)
        self.rejects([valid], gateway.INVALID_REQUEST, 400)
        self.rejects(valid | {"params": ["a"]}, gateway.INVALID_PARAMS)
        self.rejects(valid | {"jsonrpc": "1.0"}, gateway.INVALID_REQUEST)
        self.rejects({k: v for k, v in valid.items() if k != "jsonrpc"}, gateway.INVALID_REQUEST)
        self.rejects(valid | {"id": True}, gateway.INVALID_REQUEST)
        self.rejects(valid | {"id": {"x": 1}}, gateway.INVALID_REQUEST)
        self.rejects(valid | {"id": "x" * 200}, gateway.INVALID_REQUEST)
        self.rejects(valid | {"method": 7}, gateway.INVALID_REQUEST)
        self.assertEqual(gateway.parse_message(json.dumps(valid).encode()), (1, "tools/list", {}))
        self.assertEqual(gateway.parse_message(json.dumps(valid | {"params": None}).encode())[2], {})

    def test_deeply_nested_requests_are_refused_before_use(self):
        nested: object = "x"
        for _ in range(gateway.MAX_DEPTH + 2):
            nested = {"a": nested}
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": nested}}
        self.rejects(message, gateway.INVALID_REQUEST, 400)


class StreamReplyTests(unittest.TestCase):
    def test_reply_is_selected_by_request_id_not_position(self):
        stream = (
            'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
            'data: {"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"mine"}]}}\n\n'
            'data: {"jsonrpc":"2.0","id":99,"method":"sampling/createMessage","params":{}}\n\n'
            'data: {"jsonrpc":"2.0","id":8,"result":{"content":[]}}\n\n'
        )
        reply = gateway._decode(stream.encode(), "text/event-stream", 7)
        self.assertEqual(reply["result"]["content"][0]["text"], "mine")

    def test_multiline_data_and_missing_reply(self):
        stream = 'data: {"jsonrpc":"2.0",\ndata: "id":3,"result":{}}\n\n'
        self.assertEqual(gateway._decode(stream.encode(), "text/event-stream", 3)["id"], 3)
        with self.assertRaises(gateway.GatewayError):
            gateway._decode(b'data: {"jsonrpc":"2.0","method":"note"}\n\n', "text/event-stream", 3)

    def test_plain_reply_to_another_request_is_refused(self):
        with self.assertRaises(gateway.GatewayError):
            gateway._decode(b'{"jsonrpc":"2.0","id":4,"result":{}}', "application/json", 3)


class SchemaPinTests(unittest.TestCase):
    def setUp(self):
        self.connector = FakeConnector(["search", "details"])
        self.connector.tools["details"]["inputSchema"] = {"type": "object", "properties": {"id": {"type": "string"}}}
        patcher = patch.object(gateway.CONNECTORS, "get", return_value=self.connector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, pinned):
        return _app(
            search={"schema_sha256": schema_digest({"type": "object"})},
            details={"schema_sha256": schema_digest(pinned)},
        )

    def test_drifted_tool_is_withheld_everywhere_and_never_dispatched(self):
        app = self.app({"type": "object", "properties": {}})
        self.assertNotIn("details", [tool["name"] for tool in gateway.list_tools("demo", app)])
        listed = {item["tool"]: item for item in gateway.find_tools("demo", app, {"category": "search"})["tools"]}
        self.assertEqual(listed["details"]["state"], "off")
        self.assertIn("Re-verify", listed["details"]["note"])
        with self.assertRaises(gateway.GatewayError):
            gateway.call_tool("demo", app, "details", {"id": "1"}, via="use_tool")
        self.assertEqual(self.connector.calls, [])

    def test_matching_pin_runs(self):
        app = self.app(self.connector.tools["details"]["inputSchema"])
        gateway.call_tool("demo", app, "details", {"id": "1"}, via="use_tool")
        self.assertEqual(len(self.connector.calls), 1)

    def test_invalid_arguments_never_reach_the_connector(self):
        app = self.app(self.connector.tools["details"]["inputSchema"])
        with self.assertRaises(gateway.GatewayError):
            gateway.call_tool("demo", app, "details", {"id": 5}, via="use_tool")
        self.assertEqual(self.connector.calls, [])

    def test_policy_pins_the_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = mcp_gateway.load_review("demo", _review(tmp).name, Path(tmp))
            server = type(
                "Server", (), {"id": "demo", "service_id": "demo", "endpoint": "http://demo/mcp", "revision": "t"}
            )()
            states = {"categories": {"search": True, "trash": True}, "tools": dict.fromkeys(review.tools, True)}
            schema = {"type": "object", "properties": {"q": {"type": "string"}}}
            control = MagicMock()
            control.mcp_server.return_value = {"tool_snapshot": [{"id": "search", "parameters": schema}]}
            with (
                patch.object(mcp_gateway, "review_for", return_value=review),
                patch.object(mcp_gateway, "tool_states", return_value=states),
                patch.object(mcp_gateway, "read_runtime_env", return_value={}),
                patch("ctl.control_state.ControlState.runtime", return_value=control),
            ):
                policy = mcp_gateway.app_policy(server, "Demo")
        self.assertEqual(policy["tools"]["search"]["schema_sha256"], schema_digest(schema))
        self.assertNotIn("schema_sha256", policy["tools"]["details"])


class OutputBoundTests(unittest.TestCase):
    def test_oversized_and_excess_items_are_left_out(self):
        image = {"type": "image", "data": "A" * (gateway.MAX_TEXT + 10), "mimeType": "image/png"}
        many = [{"type": "text", "text": "x"}] * (gateway.MAX_CONTENT_ITEMS + 5)
        bounded = gateway._bounded({"content": [image, *many]})
        self.assertLessEqual(len(bounded["content"]), gateway.MAX_CONTENT_ITEMS + 1)
        # The oversized image plus the six items past the cap.
        self.assertIn("left out 7", bounded["content"][-1]["text"])
        self.assertLessEqual(len(json.dumps(bounded)), gateway.MAX_TEXT * 2)

    def test_deep_or_large_structure_is_dropped(self):
        deep: object = 1
        for _ in range(gateway.MAX_DEPTH + 2):
            deep = [deep]
        self.assertNotIn("structuredContent", gateway._bounded({"content": [], "structuredContent": {"d": deep}}))
        self.assertEqual(gateway._bounded({"content": "not a list"})["content"], [])


class RateLimitTests(unittest.TestCase):
    def test_bucket_refills_and_reports_wait(self):
        now = [0.0]
        limiter = gateway.RateLimiter(2, 1.0, clock=lambda: now[0])
        self.assertEqual([limiter.take("a"), limiter.take("a")], [0, 0])
        self.assertEqual(limiter.take("a"), 1)
        self.assertEqual(limiter.take("b"), 0)
        now[0] = 1.0
        self.assertEqual(limiter.take("a"), 0)

    def test_bucket_table_stays_bounded(self):
        limiter = gateway.RateLimiter(1, 0.001, limit=10)
        for index in range(50):
            limiter.take(str(index))
        self.assertLessEqual(len(limiter._buckets), 50)
        limiter = gateway.RateLimiter(5, 1.0, limit=10)
        for index in range(50):
            limiter.take(str(index))
        self.assertLessEqual(len(limiter._buckets), 11)


class ConnectorBoundTests(unittest.TestCase):
    def test_busy_connector_answers_instead_of_queueing_forever(self):
        connector = gateway.Connector({"url": "http://upstream.invalid/mcp"})
        connector._lock.acquire()
        try:
            with patch.object(gateway, "CONNECTOR_WAIT", 0.05), self.assertRaises(gateway.GatewayError) as raised:
                connector.request("tools/list", {}, retry_safe=True)
            self.assertIn("busy", str(raised.exception))
        finally:
            connector._lock.release()

    def test_slow_upstream_body_stops_at_the_deadline(self):
        class Trickle:
            def read1(self, _size):
                time.sleep(0.02)
                return b"x"

        with self.assertRaises(TimeoutError):
            gateway._read(Trickle(), time.monotonic() + 0.1)

    def test_oversized_upstream_body_is_refused(self):
        class Flood:
            def read1(self, size):
                return b"x" * size

        with self.assertRaises(gateway.GatewayError):
            gateway._read(Flood(), time.monotonic() + 5)


class HttpLimitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "policy.json"
        publish_fixture(path, {"apps": {"demo": _app()}})
        self.policy_path = path
        authority = Authority(path.parent / "authority")
        authority.grant_subject("operator", operator=True)
        self.token = authority.credential("operator", "demo")
        self.connector = FakeConnector(["search", "details"])
        for target, value in (
            ("POLICY", gateway.PolicyStore(str(path))),
            ("AUTHORITY", authority),
            ("CALL_LOG", str(Path(tmp.name) / "calls.jsonl")),
            ("REQUESTS", gateway.RateLimiter(*gateway.REQUEST_RATE)),
            ("CALLS", gateway.RateLimiter(*gateway.CALL_RATE)),
            ("CAPACITY", gateway.Capacity()),
        ):
            patcher = patch.object(gateway, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(gateway.CONNECTORS, "get", return_value=self.connector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, max_connections=gateway.MAX_CONNECTIONS):
        server = gateway.BoundedServer(("127.0.0.1", 0), gateway.Handler, max_connections=max_connections)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server.server_port

    def post(self, port, body: bytes | dict, path="/apps/demo/mcp"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"null"), dict(response.headers)
        except HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read() or b"null"), dict(exc.headers)

    def call(self, name="search", arguments=None, **params):
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}} | params,
        }

    def test_malformed_requests_are_answered_without_reaching_the_connector(self):
        port = self.serve()
        status, body, _ = self.post(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]})
        self.assertEqual((status, body["error"]["code"]), (200, gateway.INVALID_PARAMS))
        status, body, _ = self.post(port, self.call(arguments=["x"]))
        self.assertEqual(body["error"]["code"], gateway.INVALID_PARAMS)
        status, body, _ = self.post(port, self.call(name=["search"]))
        self.assertEqual(body["error"]["code"], gateway.INVALID_PARAMS)
        status, body, _ = self.post(port, b"[]")
        self.assertEqual((status, body["error"]["code"]), (400, gateway.INVALID_REQUEST))
        status, body, _ = self.post(port, b"{")
        self.assertEqual((status, body["error"]["code"]), (400, gateway.PARSE_ERROR))
        status, _body, _ = self.post(port, b"x" * (gateway.MAX_REQUEST + 1))
        self.assertEqual(status, 413)
        self.assertEqual(self.connector.calls, [])
        status, body, _ = self.post(port, self.call())
        self.assertEqual(status, 200)
        self.assertFalse(body["result"]["isError"])

    def test_tool_calls_are_rate_limited_per_person_with_retry_after(self):
        port = self.serve()
        with patch.object(gateway, "CALLS", gateway.RateLimiter(2, 0.01)):
            statuses = [self.post(port, self.call())[0] for _ in range(3)]
            status, body, headers = self.post(port, self.call())
        self.assertEqual(statuses, [200, 200, 429])
        self.assertEqual(status, 429)
        self.assertIn("slow down", body["error"]["message"])
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(len(self.connector.calls), 2)

    def test_concurrent_slow_tools_are_bounded_per_person(self):
        release = threading.Event()
        started = threading.Semaphore(0)

        def slow(method, params, *, retry_safe=False):
            started.release()
            release.wait(5)
            return {"content": [{"type": "text", "text": "done"}]}

        self.connector.request = slow
        port = self.serve()
        results = []
        with patch.object(gateway, "CAPACITY", gateway.Capacity(per_principal=2)):
            workers = [threading.Thread(target=lambda: results.append(self.post(port, self.call()))) for _ in range(2)]
            for worker in workers:
                worker.start()
            for _ in workers:
                self.assertTrue(started.acquire(timeout=5))
            status, body, _headers = self.post(port, self.call())
            release.set()
            for worker in workers:
                worker.join(timeout=5)
        self.assertEqual(status, 429)
        self.assertIn("already running", body["error"]["message"])
        self.assertEqual(sorted(result[0] for result in results), [200, 200])

    def test_slow_client_body_is_cut_off(self):
        port = self.serve()
        with (
            patch.object(gateway, "BODY_DEADLINE", 0.3),
            socket.create_connection(("127.0.0.1", port), timeout=5) as client,
        ):
            client.sendall(
                b"POST /apps/demo/mcp HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer "
                + self.token.encode()
                + b"\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
            )
            started = time.monotonic()
            reply = client.recv(4096)
        self.assertTrue(reply.startswith(b"HTTP/1.1 408"), reply[:40])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.connector.calls, [])

    def test_connections_beyond_capacity_are_refused_not_queued(self):
        port = self.serve(max_connections=1)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as held:
            time.sleep(0.1)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as second:
                second.sendall(b"GET /live HTTP/1.1\r\nHost: x\r\n\r\n")
                self.assertTrue(second.recv(4096).startswith(b"HTTP/1.1 503"))
            held.sendall(b"GET /live HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            self.assertTrue(held.recv(4096).startswith(b"HTTP/1.1 200"))

    def test_liveness_is_separate_from_readiness(self):
        port = self.serve()
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            health = json.loads(response.read())
        self.assertEqual(health["capacity"]["max_connections"], gateway.MAX_CONNECTIONS)
        self.assertIn("rate_limited", health["capacity"])
        self.policy_path.unlink()
        with self.assertRaises(HTTPError) as raised:
            urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
        self.assertEqual(raised.exception.code, 503)
        raised.exception.close()
        with urlopen(f"http://127.0.0.1:{port}/live", timeout=5) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
