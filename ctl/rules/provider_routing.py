"""Prepare an app's private provider configuration before Compose reads it."""

from ctl.core_wiring import RoutingParams, write_routing
from ctl.engine.hooks import HookContext
from ctl.rules import Rule, register


@register
class ProviderRouting(Rule):
    name = "provider_routing"
    Params = RoutingParams
    params: RoutingParams
    summary = "Writes the private provider routing contract before the app starts."

    def before_start(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        ctx.stage("provider_routing", "Preparing private AI provider routing.")
        write_routing(ctx.app, self.params, ctx.facts.paths, ctx.facts.catalog)

    def rewire(self, ctx: HookContext) -> None:
        # A speech app arriving or leaving changes the routed models.
        self.before_start(ctx)

    def after_healthy(self, ctx: HookContext) -> None:
        if self.params.mode == "gateway":
            # The account hook has confirmed login; remove the first-start password
            # from the config. Existing installations never need that section again.
            self.before_start(ctx)
