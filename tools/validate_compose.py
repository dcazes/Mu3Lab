"""Validate rendered app projects and connector Compose files in a temporary runtime.

This executes `docker compose config` only: it never starts a container,
creates a Docker network or reads/writes production runtime files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ctl.engine.project import Facts, render
from ctl.manifest.catalog import load
from ctl.runtime import RuntimePaths
from ctl.secrets import runtime_env_text

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    catalog = load()
    with tempfile.TemporaryDirectory(prefix="mu3lab-compose-check-") as folder:
        paths = RuntimePaths(Path(folder))
        facts = Facts("compose-test.example.ts.net", paths, catalog)
        env = {
            **os.environ,
            "MU3LAB_DATA_ROOT": str(paths.data),
            "MU3LAB_INGRESS_TOKEN": "test-only",
            "AUTHENTIK_ENV_FILE": "/dev/null",
            "MU3LAB_VAULT_ENV_FILE": "/dev/null",
            "MU3LAB_UID": str(os.getuid()),
            "MU3LAB_GID": str(os.getgid()),
        }
        # Explicit manifest defaults for first-start rules and required settings.
        projects = []
        for app in catalog.apps:
            project = render(app, facts)
            values = {field.env: "test-only" for field in app.manifest.configuration if field.required}
            if app.manifest.sign_in.oidc:
                oidc = app.manifest.sign_in.oidc
                values[oidc.env.client_id] = oidc.client_id
            envfile = project / ".env"
            envfile.write_text(envfile.read_text() + runtime_env_text(values))
            projects.append(project / "docker-compose.yml")
            for connector in app.connectors:
                # Clone source into a private temp folder: no `.env` in the checkout.
                source = app.connector_folder(connector.id)
                target = paths.projects / ("mcp-" + connector.id)
                shutil.copytree(source, target)
                settings = {field.env: "test-only" for field in connector.credentials}
                settings.update({secret.env: "test-only" for secret in connector.secrets})
                (target / ".env").write_text(runtime_env_text(settings))
                projects.append(target / "docker-compose.yml")
        for source in (ROOT / "platform").glob("*/docker-compose.yml"):
            target = paths.projects / ("platform-" + source.parent.name)
            shutil.copytree(source.parent, target)
            (target / ".env").write_text("")
            projects.append(target / "docker-compose.yml")
        for compose in projects:
            proc = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(compose.parent / ".env"),
                    "-f",
                    str(compose),
                    "config",
                    "--quiet",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode:
                print(compose.parent.name + ": " + proc.stderr.strip())
                return 1
        print(f"{len(projects)} rendered Compose projects validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
