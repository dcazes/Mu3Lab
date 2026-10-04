"""Prepare already connected people's assistants after chat starts."""

from ctl.engine.hooks import AppHooks, HookContext
from ctl.lobehub_ops import sync_agents


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ready, detail = sync_agents(ctx.log)
        if not ready:
            ctx.fail("chat_policy", "chat_sync_failed", detail)
