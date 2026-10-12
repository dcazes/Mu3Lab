"""Unsloth Studio is set up through its own API.

Studio's owner account belongs to Mu3Lab (its password is a generated secret).
At install Mu3Lab signs in with it, mints the API key LiteLLM uses for chat
and search, and turns on the two settings model routing needs. Each
administrator then gets a personal Studio account (rule per_person_logins):
created with a one-time setup code, given a generated password, and saved to
their vault. Studio has no single sign-on, and Authentik admits only
administrators to it.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.integrations.unsloth_studio import KEY_ENV, KEY_NAME, PASSWORD_ENV, Studio, StudioError
from ctl.store import person_logins

# Studio's own owner account, whose password Mu3Lab generated (UNSLOTH_STUDIO_PASSWORD).
OWNER = "unsloth"
READY_SECONDS = 900


def _owner(ctx: HookContext) -> Studio:
    studio = Studio(ctx.app.manifest.service.local_port)
    studio.login(OWNER, ctx.env()[PASSWORD_ENV])
    return studio


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        port = ctx.app.manifest.service.local_port
        deadline = time.monotonic() + READY_SECONDS
        while True:
            try:
                studio = _owner(ctx)
                break
            except StudioError as exc:
                if time.monotonic() > deadline:
                    ctx.fail("account_bootstrap", "account_provisioning_failed", str(exc))
                time.sleep(5)
        with studio:
            key = ctx.env().get(KEY_ENV, "")
            with Studio(port, token=key) as check:
                works = bool(key) and check.key_works()
            if not works:
                ctx.set_env({KEY_ENV: studio.create_key(KEY_NAME)})
            studio.prepare_routing()
        return "Unsloth Studio is ready for Mu3Lab's chat and search requests."

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        paths = ctx.facts.paths if ctx.facts else None
        with _owner(ctx) as studio:
            accounts = {str(item["username"]).casefold(): item for item in studio.accounts()}
            for person in people:
                username = person["username"]
                if username.casefold() == OWNER:
                    ctx.log(f"{username} shares its name with Studio's own account, so it gets no Studio login.")
                    continue
                account = accounts.get(username.casefold())
                if not person["member"]:
                    if account and account.get("is_active"):
                        studio.set_active(str(account["account_id"]), False)
                    continue
                login = person_logins.get(ctx.app.id, username, paths)
                if account is None:
                    setup = studio.create_account(username)
                elif login is None or account.get("setup_code_pending"):
                    # Mu3Lab lost the password or never finished: give the account a fresh one.
                    setup = studio.new_setup_code(str(account["account_id"]))
                else:
                    if not account.get("is_active"):
                        studio.set_active(str(account["account_id"]), True)
                    continue
                if account is not None and not account.get("is_active"):
                    studio.set_active(str(account["account_id"]), True)
                password = secrets.token_urlsafe(24)
                studio.set_password(str(setup["account"]["username"]), str(setup["setup_code"]), password)
                person_logins.put(ctx.app.id, username, str(setup["account"]["username"]), password, paths)
