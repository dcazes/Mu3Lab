"""Keep an app on the home network listening on this server's current home address.

An app kept off the tailnet (Jellyfin, for the household's TVs) publishes its
port on the home address only (``${MU3LAB_HOME_IP}``). Routers hand that
address out and can change it, which is why the owner is asked to reserve it.
If it changes anyway, this rule notices within a minute, renders the app's
settings again with the new address, restarts it if it was running, and runs
the app's own after-start setup again (``hooks.after_healthy``) so anything
built from the home network, such as Jellyfin's list of local networks,
follows. TVs and phones then need the new address typed in once.
"""

from __future__ import annotations

from ctl import resource_locks
from ctl.control_state import ControlState
from ctl.engine.hooks import HookContext, StepFailed, load_app_hooks
from ctl.rules import Params, Rule, register

ADDRESS_ENV = "MU3LAB_HOME_IP"
NETWORK_ENV = "MU3LAB_HOME_NETWORK"


class HomeNetworkParams(Params):
    pass


def _running(ctx: HookContext) -> bool:
    state = ControlState.runtime(ctx.facts.paths) if ctx.facts else None
    installation = state.installation(ctx.app.id) if state else None
    return bool(installation and installation["state"] in {"running", "degraded"})


@register
class HomeNetwork(Rule):
    name = "home_network"
    Params = HomeNetworkParams
    summary = "Follows this server's home-network address if the router changes it."
    params: HomeNetworkParams

    def periodic(self, ctx: HookContext) -> str:
        if ctx.facts is None:
            return ""
        current = ctx.facts.home_address
        env = ctx.env()
        if current is None or (env.get(ADDRESS_ENV), env.get(NETWORK_ENV)) == (current.ip, current.network):
            # Off the network for now: keep the last address rather than drop the app.
            return ""
        before = env.get(ADDRESS_ENV) or "none"
        try:
            with resource_locks.hold(f"app:{ctx.app.id}", timeout=5, paths=ctx.facts.paths):
                ctx.rerender()
                if not _running(ctx):
                    return f"{ctx.app.manifest.name} will use the new home address {current.ip} when it next starts."
                wait = ctx.app.manifest.service.start_timeout_seconds
                rc, output = ctx.compose.up(ctx.log, wait_seconds=wait, recreate=True)
                if rc:
                    return f"{ctx.app.manifest.name} could not move to the new home address {current.ip}: {output}"
                load_app_hooks(ctx.app).after_healthy(ctx)
        except resource_locks.Busy:
            return ""  # another job holds the app; the next minute tries again
        except (StepFailed, OSError, ValueError, RuntimeError) as exc:
            return f"{ctx.app.manifest.name} could not finish moving to {current.ip}: {exc}"
        return (
            f"This server's home address changed from {before} to {current.ip}; "
            f"{ctx.app.manifest.name} now listens there. TVs and phones need the new address."
        )
