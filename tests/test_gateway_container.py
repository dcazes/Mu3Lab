"""Opt-in smoke test for the built, non-root gateway with production mount boundaries.

Build the gateway image first and set MU3LAB_TEST_GATEWAY_IMAGE to its local tag.
Only this test's uniquely named disposable container is created/removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from gateway_authority import Authority
from tests.test_mcp_gateway import _app


@unittest.skipUnless(os.environ.get("MU3LAB_TEST_GATEWAY_IMAGE"), "built gateway image not supplied")
class GatewayContainerTests(unittest.TestCase):
    def test_private_mounts_person_bound_access_and_policy_loss(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="mu3lab-gateway-container-"))
        root = Path(temporary)
        (root / "policy").mkdir()
        (root / "logs").mkdir()
        store = Authority(root / "authority")
        revision = store.invalidate()
        payload = json.dumps({"version": 2, "revision": revision, "apps": {"demo": _app()}}).encode()
        policy = root / "policy" / "policy.json"
        policy.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        store.publish(revision, digest)
        store.grant_subject("operator", operator=True)
        token = store.credential("operator", "demo")
        name = f"mu3lab-test-gateway-{uuid4().hex}"
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "-p",
                "127.0.0.1::8080",
                "-v",
                f"{root / 'policy'}:/config:ro",
                "-v",
                f"{root / 'logs'}:/logs",
                "-v",
                f"{root / 'authority'}:/authority",
                os.environ["MU3LAB_TEST_GATEWAY_IMAGE"],
            ],
            check=True,
            capture_output=True,
        )
        port = subprocess.check_output(["docker", "port", name, "8080/tcp"], text=True).strip().split(":")[-1]
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        while True:
            try:
                with urlopen(base + "/health", timeout=1) as response:
                    health = json.load(response)
                break
            except OSError:
                if time.monotonic() > deadline:
                    self.fail("Disposable gateway did not become ready.")
                time.sleep(0.1)
        self.assertEqual(health["revision"], revision)
        self.assertEqual(health["policy_sha256"], digest)
        store.check_policy(revision, digest)
        self.assertEqual(store.policy_state()["acknowledged"], revision)

        def ping(bearer):
            request = Request(
                base + "/apps/demo/mcp",
                data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
            )
            return urlopen(request, timeout=3)

        with ping(token) as response:
            self.assertEqual(json.load(response)["result"], {})
        with self.assertRaises(HTTPError) as refused:
            ping("retired-app-wide-token")
        self.assertEqual(refused.exception.code, 401)
        refused.exception.close()
        policy.unlink()
        with self.assertRaises(HTTPError) as unavailable:
            urlopen(base + "/health", timeout=3)
        self.assertEqual(unavailable.exception.code, 503)
        unavailable.exception.close()
        with self.assertRaises(HTTPError) as denied:
            ping(token)
        self.assertEqual(denied.exception.code, 503)
        denied.exception.close()
