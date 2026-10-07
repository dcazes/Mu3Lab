"""Run a setup script from the app's folder inside one of its containers.

Used for setup an app supports from its own tooling but not from
configuration: registering Authentik as a Django sign-in provider, adopting
a seeded administrator, configuring Nextcloud apps through ``occ``. The script
reaches the container on standard input; ``MU3LAB_OUTPUT key=value`` lines it
prints are saved to the app's private settings.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ctl.engine import template
from ctl.engine.hooks import HookContext
from ctl.engine.project import lookup_for
from ctl.engine.template import TemplateError
from ctl.rules import Params, Rule, RuleError, register

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class ContainerScriptParams(Params):
    at: Literal["after_start", "after_healthy"]
    service: str
    script: str
    interpreter: tuple[str, ...] = Field(min_length=1)
    # "{{owner_json}}" becomes the installing owner's identity; other placeholders
    # ({{dns_name}}, {{public_url}}) are filled like manifest settings.
    args: tuple[str, ...] = ()
    expect: str = ""  # a line the script prints on success
    needs_owner: bool = False
    purpose: str
    timeout_seconds: int = Field(default=300, ge=10, le=1800)


@register
class ContainerScript(Rule):
    name = "container_script"
    Params = ContainerScriptParams
    summary = "Runs a setup script from the app's folder inside one of its containers."
    params: ContainerScriptParams

    @property
    def needs_owner(self) -> bool:
        return self.params.needs_owner

    def check_files(self, app: App) -> None:
        if not (app.folder / self.params.script).is_file():
            raise RuleError(f"{app.id}: container_script needs {self.params.script}")

    def _run(self, ctx: HookContext) -> None:
        params = self.params
        owner = json.dumps(dict(ctx.owner)) if ctx.owner else ""
        if params.needs_owner and not owner:
            ctx.fail(
                "configure_application", "identity_email_missing", f"{params.purpose} needs your Authentik identity."
            )
        try:
            args = [owner if arg == "{{owner_json}}" else self._fact(ctx, arg) for arg in params.args]
        except TemplateError as exc:
            ctx.fail("configure_application", "application_configuration_failed", f"{params.purpose}: {exc}")
            return
        ctx.stage("configure_application", params.purpose + ".")
        result = ctx.run_script(
            params.service, params.script, params.interpreter, args=args, timeout=params.timeout_seconds
        )
        if not result.ok or (params.expect and params.expect not in result.raw):
            ctx.fail(
                "configure_application",
                "application_configuration_failed",
                f"{ctx.app.manifest.name} setup step failed: {params.purpose}. {result.error}".strip(),
            )
        outputs = {key: value for key, value in result.outputs.items() if key.isupper()}
        if outputs:
            ctx.set_env(outputs)

    @staticmethod
    def _fact(ctx: HookContext, arg: str) -> str:
        """Fill ``{{dns_name}}`` and other known facts the way manifest settings are filled."""
        if ctx.facts is None:
            return arg
        return template.render(arg, lookup_for(ctx.app, ctx.facts, ctx.env()))

    def after_start(self, ctx: HookContext) -> None:
        if self.params.at == "after_start":
            self._run(ctx)

    def after_healthy(self, ctx: HookContext) -> None:
        if self.params.at == "after_healthy":
            self._run(ctx)
