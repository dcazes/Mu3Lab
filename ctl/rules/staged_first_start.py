"""Start some containers first, run a setup script, then start the rest.

For apps whose health check only passes after a setup step (Nextcloud reports
unhealthy until its database installation completes): waiting on health
before that step would wait forever. The listed containers start without
Compose's health wait, the script runs, and the engine then starts every
container and waits for health as usual.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ctl.engine.hooks import HookContext, StartPlan
from ctl.rules import Params, Rule, RuleError, register

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class SetupScript(Params):
    service: str
    script: str
    interpreter: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: int = Field(default=900, ge=10, le=3600)
    purpose: str


class StagedFirstStartParams(Params):
    first: tuple[str, ...] = Field(min_length=1)
    then: SetupScript | None = None


@register
class StagedFirstStart(Rule):
    name = "staged_first_start"
    Params = StagedFirstStartParams
    summary = "Starts some containers first and runs a setup script before waiting on health."
    params: StagedFirstStartParams

    def check_files(self, app: App) -> None:
        then = self.params.then
        if then and not (app.folder / then.script).is_file():
            raise RuleError(f"{app.id}: staged_first_start needs {then.script}")

    def plan_start(self, ctx: HookContext, plan: StartPlan) -> None:
        plan.services = list(self.params.first)
        plan.wait = False

    def after_start(self, ctx: HookContext) -> None:
        then = self.params.then
        if then is None:
            return
        ctx.stage("base_installation", then.purpose + ".")
        result = ctx.run_script(then.service, then.script, then.interpreter, timeout=then.timeout_seconds)
        if not result.ok:
            ctx.fail("base_installation", "base_installation_incomplete", f"{then.purpose} failed: {result.error}")
        if result.outputs:
            ctx.set_env({key: value for key, value in result.outputs.items() if key.isupper()})
