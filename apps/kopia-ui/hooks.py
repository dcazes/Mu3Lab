"""Kopia's own step: the login for its administrators-only, read-only chat connector.

Kopia has no read-only account, so the connector uses the control login Mu3Lab
already keeps (``mu3lab-control``); the adapter itself only calls Kopia's two
read routes (platform/mcp-adapter/server.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ctl.engine.hooks import AppHooks
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

if TYPE_CHECKING:
    from ctl.manifest.catalog import App

PASSWORD_ENV = "KOPIA_SERVER_CONTROL_PASSWORD"


class Hooks(AppHooks):
    def connector_credentials(self, app: App, connector_id: str, paths: RuntimePaths) -> dict[str, str]:
        password = read_runtime_env(paths.projects / app.id / ".env").get(PASSWORD_ENV, "")
        if not password:
            raise RuntimeError("Kopia has no control password yet; start the Kopia backups app first.")
        return {"password": password}
