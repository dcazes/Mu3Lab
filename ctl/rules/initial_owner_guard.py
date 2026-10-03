"""Admit only the installing owner until they have signed in, then the whole household.

Some apps (Actual Budget) make the first person who signs in through Authentik
their owner. Until the installing owner has, Authentik admits only them (the
username in ``env`` is written into the app's Authentik policy). A read-only
query against the app's own database, kept in the manifest, says when they
have; the guard is then lifted and the sign-in re-registered.
"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, register
from ctl.workflow_secrets import JobIdentity

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class OwnerSignedIn(Params):
    sqlite: str  # database file under the app's data folder
    query: str  # returns a row once the owner has signed in; binds :username and :now


class InitialOwnerGuardParams(Params):
    env: str = "MU3LAB_INITIAL_OWNER_USERNAME"
    owner_signed_in: OwnerSignedIn


@register
class InitialOwnerGuard(Rule):
    name = "initial_owner_guard"
    Params = InitialOwnerGuardParams
    summary = "Admits only the installing owner until they have signed in once."
    params: InitialOwnerGuardParams

    @property
    def needs_owner(self) -> bool:
        return True

    def prepare_env(self, app: App, env: dict[str, str], owner: JobIdentity | None) -> None:
        # Set once on the first install; "" afterwards means the guard was lifted.
        if self.params.env not in env and owner:
            env[self.params.env] = owner["username"]

    def periodic(self, ctx: HookContext) -> str:
        username = ctx.env().get(self.params.env, "")
        if not username or ctx.facts is None:
            return ""
        database = ctx.facts.paths.data / ctx.app.manifest.data_folder / self.params.owner_signed_in.sqlite
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5) as connection:
                row = connection.execute(
                    self.params.owner_signed_in.query, {"username": username, "now": int(time.time())}
                ).fetchone()
        except (OSError, sqlite3.Error):
            return ""
        if not row:
            return ""
        ctx.set_env({self.params.env: ""})
        ctx.reregister_sign_in()
        return f"{ctx.app.manifest.name} is now open to the whole household."
