"""Run the real vault integration against a disposable project, then remove it."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    password = "isolated-vault-admin-test-only-2026"
    with tempfile.TemporaryDirectory(prefix="mu3lab-vault-test-runtime-") as folder:
        env = {
            **os.environ,
            "MU3LAB_TEST_VAULT_ADMIN_PASSWORD": password,
            "MU3LAB_TEST_VAULT_ADMIN_TOKEN": Argon2id(
                salt=os.urandom(16), length=32, iterations=3, lanes=4, memory_cost=65536
            ).derive_phc_encoded(password.encode()),
            "MU3LAB_VAULT_INTEGRATION": "1",
            "MU3LAB_RUNTIME_ROOT": folder,
        }
        command = ["docker", "compose", "-f", str(ROOT / "tests/integration/vaultwarden-compose.yml")]
        try:
            subprocess.run([*command, "up", "-d"], env=env, check=True)
            for _ in range(90):
                try:
                    if httpx.get("http://127.0.0.1:19902/alive", timeout=2).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Isolated Vaultwarden did not start")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "tests.integration.test_vaultwarden_live", "-v"],
                cwd=ROOT,
                env=env,
                check=False,
            ).returncode
        finally:
            subprocess.run([*command, "down", "-v"], env=env, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
