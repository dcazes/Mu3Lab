"""Switch off the app's own password login once its owner account exists.

Some apps (Immich) create their administrator only through a password API,
so password login must stay on until that account exists. Afterwards this
rule flips the app's setting, regenerates its configuration files and
restarts it, leaving Authentik as the only way in.
"""

from __future__ import annotations

from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, register


class PasswordLoginOffParams(Params):
    env: str  # the generated setting that holds "true" until setup is done


@register
class PasswordLoginOffAfterSetup(Rule):
    name = "password_login_off_after_setup"
    Params = PasswordLoginOffParams
    summary = "Turns off the app's own password login after its owner account exists."
    params: PasswordLoginOffParams

    def after_healthy(self, ctx: HookContext) -> None:
        if ctx.env().get(self.params.env) == "false":
            return
        ctx.stage("configure_application", f"Switching {ctx.app.manifest.name} to Authentik-only sign-in.")
        ctx.set_env({self.params.env: "false"})
        ctx.rerender()
        rc, _output = ctx.compose.up(
            ctx.log, wait_seconds=ctx.app.manifest.service.start_timeout_seconds, recreate=True
        )
        if rc:
            ctx.fail(
                "configure_application",
                "application_configuration_failed",
                f"{ctx.app.manifest.name} could not switch to Authentik-only sign-in.",
            )
