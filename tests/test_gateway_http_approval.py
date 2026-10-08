"""Real HTTP dispatch proves approval, person scope and ambiguous-write containment."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

from ctl import mcp_gateway
from ctl.api import create_app
from tests.test_mcp_gateway import _app, gateway
from tests.test_provider_onboarding import OPERATOR_WITH_CSRF

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body["method"]
        if method == "initialize":
            result = {}
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif method == "tools/list":
            result = {"tools": [{"name": name, "inputSchema": SCHEMA} for name in ("rename", "search")]}
        elif body["params"]["name"] == "search":
            result = {"content": [{"type": "text", "text": "found"}]}
        else:
            self.server.writes.append(body["params"])
            if self.server.disconnect:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            result = {"content": [{"type": "text", "text": "changed"}]}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "session")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def serve(test, handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def close():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    test.addCleanup(close)
    return server


class GatewayApprovalHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.upstream = serve(self, Upstream)
        self.upstream.writes, self.upstream.disconnect = [], False
        self.app = _app(rename={"enabled": True, "core": True}) | {"approval_required": True}
        self.app["upstream"] = {"url": f"http://127.0.0.1:{self.upstream.server_port}/mcp"}
        patcher = patch.object(mcp_gateway, "project", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        mcp_gateway.write_policy({"apps": {"demo": self.app}})
        self.store = mcp_gateway.authority()
        self.store.grant_subject("subject", operator=True)
        self.token = self.store.credential("subject", "demo")
        gateway.CONNECTORS.forget()
        self.addCleanup(gateway.CONNECTORS.forget)
        for target, value in (
            ("POLICY", gateway.PolicyStore(str(self.root / "policy" / "policy.json"))),
            ("AUTHORITY", self.store),
            ("CALL_LOG", str(self.root / "calls.jsonl")),
        ):
            patcher = patch.object(gateway, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.gateway = serve(self, gateway.Handler)
        for target, value in (
            ("ctl.mcp_gateway.PORT", self.gateway.server_port),
            ("ctl.api.security.ingress_token", "real-token"),
            ("ctl.api.security.csrf_token", "bound"),
            ("ctl.people.operator_subjects", {"subject"}),
        ):
            patcher = patch(target, return_value=value) if not isinstance(value, int) else patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app())

    def call(self, name="rename", arguments=None, *, token=None):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {"name": "private"}},
        }
        request = Request(
            f"http://127.0.0.1:{self.gateway.server_port}/apps/demo/mcp",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token or self.token}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())["result"]

    def execute(self, operation):
        request = Request(
            f"http://127.0.0.1:{self.gateway.server_port}/apps/demo/operations/{operation}/execute",
            data=b"{}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_direct_helper_and_console_approval_share_one_dispatch(self):
        first = self.call()["structuredContent"]["operation_id"]
        helper = self.call("change_with_tool", {"tool": "rename", "arguments": {"name": "private"}})
        self.assertEqual(helper["structuredContent"]["operation_id"], first)
        self.assertTrue(self.call("use_tool", {"tool": "rename", "arguments": {"name": "private"}})["isError"])
        self.assertEqual(self.upstream.writes, [])
        detail = self.client.get(f"/api/v1/tool-approvals/{first}", headers=OPERATOR_WITH_CSRF)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["operation"]["arguments"], {"name": "private"})
        approved = self.client.post(
            f"/api/v1/tool-approvals/{first}/decision", headers=OPERATOR_WITH_CSRF, json={"decision": "approve"}
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["operation"]["state"], "succeeded")
        self.assertEqual(len(self.upstream.writes), 1)
        self.assertEqual(self.execute(first)["outcome"], "succeeded")
        self.assertEqual(len(self.upstream.writes), 1)
        self.assertNotIn("private", (self.root / "calls.jsonl").read_text())

    def test_wrong_person_member_missing_csrf_and_pending_execution_never_write(self):
        operation = self.call()["structuredContent"]["operation_id"]
        base = f"/api/v1/tool-approvals/{operation}"
        other = OPERATOR_WITH_CSRF | {"x-authentik-uid": "different"}
        self.assertEqual(self.client.get(base, headers=other).status_code, 404)
        member = OPERATOR_WITH_CSRF | {"x-authentik-groups": "mu3lab-household"}
        self.assertEqual(self.client.get(base, headers=member).status_code, 403)
        no_csrf = {key: value for key, value in OPERATOR_WITH_CSRF.items() if key != "x-mu3lab-csrf"}
        self.assertEqual(
            self.client.post(base + "/decision", headers=no_csrf, json={"decision": "approve"}).status_code, 403
        )
        self.execute(operation)
        self.assertEqual(self.upstream.writes, [])
        with self.assertRaises(HTTPError) as refused:
            self.call(token="old-app-token")
        self.assertEqual(refused.exception.code, 401)
        refused.exception.close()

    def test_commit_then_disconnect_is_unknown_and_never_dispatched_again(self):
        operation = self.call()["structuredContent"]["operation_id"]
        self.store.decide(operation, "subject", approve=True)
        self.upstream.disconnect = True
        result = self.execute(operation)
        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertEqual(len(self.upstream.writes), 1)
        self.assertEqual(self.execute(operation)["outcome"], "outcome_unknown")
        self.assertEqual(len(self.upstream.writes), 1)

    def test_two_http_dispatches_consume_one_approval(self):
        operation = self.call()["structuredContent"]["operation_id"]
        self.store.decide(operation, "subject", approve=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.execute(operation), range(2)))
        self.assertEqual(len(self.upstream.writes), 1)

    def test_console_nonce_is_the_same_operation_and_changed_inputs_cannot_replay(self):
        operation = self.call()["structuredContent"]["operation_id"]
        server = SimpleNamespace(gateway=True, service_id="demo", id="demo")
        tool = {"risk": "write", "parameters": SCHEMA}
        path = "/api/v1/mcp/servers/demo/tools/rename"
        with patch("ctl.mcp_console._resolve", return_value=(server, tool, "needs_approval")):
            prepared = self.client.post(
                path + "/prepare", headers=OPERATOR_WITH_CSRF, json={"arguments": {"name": "private"}}
            )
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertEqual(prepared.json()["confirmation_token"], operation)
            changed = self.client.post(
                path + "/execute",
                headers=OPERATOR_WITH_CSRF,
                json={"arguments": {"name": "different"}, "confirmation_token": operation},
            )
            self.assertEqual(changed.status_code, 422)
            self.assertEqual(self.upstream.writes, [])
            accepted = self.client.post(
                path + "/execute",
                headers=OPERATOR_WITH_CSRF,
                json={"arguments": {"name": "private"}, "confirmation_token": operation},
            )
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.json()["outcome"], "succeeded")
            changed = self.client.post(
                path + "/execute",
                headers=OPERATOR_WITH_CSRF,
                json={"arguments": {"name": "different"}, "confirmation_token": operation},
            )
            self.assertEqual(changed.status_code, 422)
        self.assertEqual(len(self.upstream.writes), 1)

    def test_permission_revocation_before_dispatch_denies_cached_approval(self):
        operation = self.call()["structuredContent"]["operation_id"]
        self.store.decide(operation, "subject", approve=True)
        with mcp_gateway.policy_change():
            pass
        with self.assertRaises(HTTPError) as error:
            self.execute(operation)
        self.assertEqual(error.exception.code, 503)
        error.exception.close()
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.store.view(operation, "subject")["state"], "revoked")

    def test_console_reads_use_scoped_gateway_and_refuse_missing_policy(self):
        server = SimpleNamespace(gateway=True, service_id="demo", id="demo", transport="streamable-http")
        tool = {"risk": "read", "parameters": SCHEMA}
        path = "/api/v1/mcp/servers/demo/tools/search/execute"
        with patch("ctl.mcp_console._resolve", return_value=(server, tool, "auto")):
            accepted = self.client.post(path, headers=OPERATOR_WITH_CSRF, json={"arguments": {"name": "private"}})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.json()["result"]["content"][0]["text"], "found")
            (self.root / "policy" / "policy.json").unlink()
            refused = self.client.post(path, headers=OPERATOR_WITH_CSRF, json={"arguments": {"name": "private"}})
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertEqual(self.upstream.writes, [])
