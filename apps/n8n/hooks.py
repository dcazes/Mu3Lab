"""n8n: its owner account is created through n8n's own setup call, and saved to the owner's vault.

Single sign-on is a paid n8n feature, so Authentik guards the address
(administrators only) and n8n keeps its own login. On a new server n8n asks
whoever opens it first to create the owner; Mu3Lab answers that same request
(``POST /rest/owner/setup``) on the loopback port with the owner's Authentik
email and a generated password, which goes to their vault for Bitwarden to fill
at n8n's address. Checked against n8n 2.48.5 (owner.controller.ts).
"""

from __future__ import annotations

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import onboarding as onboarding_state


def _setup_pending(port: int) -> bool:
    settings = (request(port, "/rest/settings") or {}).get("data") or {}
    return bool((settings.get("userManagement") or {}).get("showSetupOnFirstLoad"))


def _signs_in(port: int, email: str, password: str) -> bool:
    try:
        reply = request(port, "/rest/login", method="POST", data={"emailOrLdapLoginId": email, "password": password})
    except LoopbackError:
        return False
    return str(((reply or {}).get("data") or {}).get("email", "")).lower() == email.lower()


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None and ctx.facts is not None
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths
        stage = "account_bootstrap"
        email = ctx.owner["email"]
        try:
            pending = _setup_pending(port)
            record = onboarding_state.read(ctx.app.id, paths)
            if not pending and (record.get("provisioned") or record.get("vault_saved")):
                return "Existing n8n kept; its login is in your vault."
            # Saved before n8n sees it, so a retry after an interrupted install uses the same one.
            login = onboarding_state.prepare_login(ctx.app.id, paths)
            if pending:
                name = (ctx.owner.get("display_name") or ctx.owner["username"]).strip()
                first, _, last = name.partition(" ")
                request(
                    port,
                    "/rest/owner/setup",
                    method="POST",
                    data={"email": email, "firstName": first, "lastName": last or first, "password": login["password"]},
                )
            if not _signs_in(port, email, login["password"]):
                ctx.fail(
                    stage,
                    "account_provisioning_failed",
                    "This n8n data has an owner Mu3Lab did not create; it can only set up a new n8n.",
                )
            onboarding_state.complete_login(ctx.app.id, email, ctx.facts.public_url(ctx.app), paths)
        except LoopbackError as exc:
            ctx.fail(stage, "account_provisioning_failed", str(exc))
        return "n8n owner created; your login is saved to your vault."
