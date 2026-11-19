"""HomeBox: one shared household inventory, signed in through Authentik.

HomeBox gives every new Authentik sign-in its own empty inventory (a "group"),
and only someone already in a group can bring another person into it, by
invitation. So Mu3Lab, through HomeBox's own API on its loopback port:

- registers the owner first (their Authentik email, a password nobody uses),
  which creates the household's inventory, and keeps a key named "Mu3Lab" for
  that account (visible, and revocable, in the owner's HomeBox settings);
- adds each household member (rule ``per_person_logins``) with a one-time
  invitation to that inventory, also with a password nobody uses.

Everyone's first Authentik sign-in then links to the account Mu3Lab made, by
email, so they land in the shared inventory. Sign-up and password sign-in stay
on inside HomeBox for these loopback calls only: the private address refuses
those paths (``route.blocked_paths``). Checked against HomeBox v0.27.1.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import onboarding as onboarding_state

KEY_ENV = "HOMEBOX_MU3LAB_KEY"
KEY_NAME = "Mu3Lab"
# HomeBox API keys always expire (30 days unless asked); Mu3Lab's lasts ten years.
KEY_YEARS = 10
API = "/api/v1"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key_works(port: int, key: str) -> bool:
    try:
        return bool((request(port, f"{API}/users/self", token=key) or {}).get("item"))
    except LoopbackError:
        return False


def _members(port: int, key: str) -> set[str]:
    listed = request(port, f"{API}/groups/members", token=key) or []
    return {str(member.get("email", "")).strip().lower() for member in listed if isinstance(member, dict)}


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None and ctx.facts is not None
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths
        stage = "account_bootstrap"
        if _key_works(port, ctx.env().get(KEY_ENV, "")):
            return "Existing HomeBox kept; Open uses Authentik."
        email = ctx.owner["email"]
        # Saved before HomeBox sees it, so a retry after an interrupted install signs in with the same one.
        login = onboarding_state.prepare_login(ctx.app.id, paths)
        try:
            try:
                request(
                    port,
                    f"{API}/users/register",
                    method="POST",
                    data={
                        "name": ctx.owner.get("display_name") or ctx.owner["username"],
                        "email": email,
                        "password": login["password"],
                    },
                    json_reply=False,
                )
            except LoopbackError:
                pass  # Already registered by an interrupted install; signing in below settles it.
            try:
                session = request(
                    port, f"{API}/users/login", method="POST", data={"username": email, "password": login["password"]}
                )
            except LoopbackError:
                ctx.fail(
                    stage,
                    "account_provisioning_failed",
                    "This HomeBox data has an owner Mu3Lab did not create; it can only set up a new inventory.",
                )
            token = str((session or {}).get("token", "")).removeprefix("Bearer ").strip()
            if not token:
                ctx.fail(stage, "account_provisioning_failed", "HomeBox did not sign its owner in.")
            created = request(
                port,
                f"{API}/users/self/api-keys",
                method="POST",
                data={"name": KEY_NAME, "expiresAt": _iso(datetime.now(UTC) + timedelta(days=365 * KEY_YEARS))},
                token=token,
            )
            key = str((created or {}).get("token", ""))
            if not key or not _key_works(port, key):
                ctx.fail(stage, "account_provisioning_failed", "HomeBox did not create Mu3Lab's key.")
            ctx.set_env({KEY_ENV: key})
            onboarding_state.complete_login(ctx.app.id, email, ctx.facts.public_url(ctx.app), paths)
        except LoopbackError as exc:
            ctx.fail(stage, "account_provisioning_failed", str(exc))
        return "HomeBox's household inventory created under your name; Open uses Authentik."

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        port = ctx.app.manifest.service.local_port
        key = ctx.env().get(KEY_ENV, "")
        if not _key_works(port, key):
            raise RuntimeError("Mu3Lab's HomeBox key is missing or was revoked.")
        try:
            joined = _members(port, key)
            for person in people:
                email = person["email"].strip().lower()
                # Leaving the household is Authentik's to enforce; their HomeBox account and history stay.
                if not person["member"] or not email or email in joined:
                    continue
                invitation = request(
                    port,
                    f"{API}/groups/invitations",
                    method="POST",
                    data={"uses": 1, "expiresAt": _iso(datetime.now(UTC) + timedelta(hours=1))},
                    token=key,
                )
                try:
                    request(
                        port,
                        f"{API}/users/register",
                        method="POST",
                        data={
                            "name": person["name"],
                            "email": email,
                            "password": secrets.token_urlsafe(32),
                            "token": str((invitation or {}).get("token", "")),
                        },
                        json_reply=False,
                    )
                except LoopbackError:
                    # They signed in before Mu3Lab added them and so have an inventory of their own; only they
                    # can join the household's (an invitation from the owner, inside HomeBox).
                    ctx.log(f"HomeBox: {person['username']} already has their own inventory; not added.")
                    continue
                joined.add(email)
                ctx.log(f"HomeBox: {person['username']} joined the household's inventory.")
        except LoopbackError as exc:
            raise RuntimeError(f"HomeBox did not add the household: {exc}") from None
