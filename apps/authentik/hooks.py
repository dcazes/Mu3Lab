"""Authentik's own step: the read-only key for its administrators-only chat assistant.

Mu3Lab applies a blueprint (ctl/authentik_blueprints.py) that creates a service
account whose only role views people, groups and sign-in events, with an API
token whose key Mu3Lab generates. The connector never holds Mu3Lab's own
Authentik administrator token.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from ctl.authentik_blueprints import CHAT_READER_BLUEPRINT, render_chat_reader_blueprint
from ctl.engine.hooks import AppHooks
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.runtime import RuntimePaths

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class Hooks(AppHooks):
    def connector_credentials(self, app: App, connector_id: str, paths: RuntimePaths) -> dict[str, str]:
        key = secrets.token_urlsafe(48)
        try:
            Authentik.runtime(paths).apply_blueprint(CHAT_READER_BLUEPRINT, render_chat_reader_blueprint(key))
        except AuthentikError as exc:
            raise RuntimeError(f"Authentik did not create the chat assistant's read-only key: {exc}") from None
        return {"api_token": key}
