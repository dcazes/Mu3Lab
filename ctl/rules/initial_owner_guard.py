"""Admit only the installing owner until they have signed in, then the whole household.

Some apps (Actual Budget) make the first person who signs in through Authentik
their owner. Until the installing owner has, Authentik admits only them (the
username in ``env`` is written into the app's Authentik policy). A read-only
check kept in the manifest says when they have: a query against the app's
SQLite database, or for apps with a database server (Outline, Dawarich) a
script from the app's folder run inside one of its containers. The guard is
then lifted and the sign-in re-registered.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, RuleError, register
from ctl.store.workflows import JobIdentity

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class OwnerScript(Params):
    service: str
    script: str  # receives the owner's username and email; prints `expect` once they have signed in
    interpreter: tuple[str, ...] = Field(min_length=1)
    expect: str
    timeout_seconds: int = Field(default=60, ge=5, le=300)


class OwnerSignedIn(Params):
    sqlite: str = ""  # database file under the app's data folder
    query: str = ""  # returns a row once the owner has signed in; binds :username and :now
    script: OwnerScript | None = None

    @model_validator(mode="after")
    def _one_check(self) -> OwnerSignedIn:
        if bool(self.sqlite and self.query) == (self.script is not None):
            raise ValueError("owner_signed_in needs either sqlite and query, or script")
        return self


class InitialOwnerGuardParams(Params):
    env: str = "MU3LAB_INITIAL_OWNER_USERNAME"
    owner_signed_in: OwnerSignedIn
    # Runs once the owner has signed in, before the household is admitted (Dawarich
    # makes the owner its administrator and removes its demo account). It receives
    # the owner as JSON and must print `expect`; until it does, the guard stays.
    finish: OwnerScript | None = None


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

    def check_files(self, app: App) -> None:
        for script in (self.params.owner_signed_in.script, self.params.finish):
            if script and not (app.folder / script.script).is_file():
                raise RuleError(f"{app.id}: initial_owner_guard needs {script.script}")

    def _run(self, ctx: HookContext, script: OwnerScript, args: list[str]) -> bool:
        result = ctx.run_script(
            script.service, script.script, script.interpreter, args=args, timeout=script.timeout_seconds
        )
        return result.ok and script.expect in result.raw

    def _signed_in(self, ctx: HookContext, username: str) -> bool:
        check = self.params.owner_signed_in
        if check.script is not None:
            email = ctx.owner["email"] if ctx.owner else ""
            return self._run(ctx, check.script, [username, email])
        assert ctx.facts is not None
        database = ctx.facts.paths.data / ctx.app.manifest.data_folder / check.sqlite
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5) as connection:
                row = connection.execute(check.query, {"username": username, "now": int(time.time())}).fetchone()
        except (OSError, sqlite3.Error):
            return False
        return bool(row)

    def periodic(self, ctx: HookContext) -> str:
        username = ctx.env().get(self.params.env, "")
        if not username or ctx.facts is None:
            return ""
        if not self._signed_in(ctx, username):
            return ""
        finish = self.params.finish
        if finish is not None and not self._run(ctx, finish, [json.dumps(dict(ctx.owner or {}))]):
            ctx.log(f"{ctx.app.manifest.name}'s owner setup is not finished yet; retrying shortly.")
            return ""
        ctx.set_env({self.params.env: ""})
        try:
            ctx.reregister_sign_in()
        except Exception:
            ctx.set_env({self.params.env: username})
            raise
        return f"{ctx.app.manifest.name} is now open to the whole household."
