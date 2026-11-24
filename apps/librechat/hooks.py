"""Add everyone's app assistants once LibreChat answers."""

from ctl.engine.hooks import AppHooks, HookContext
from ctl.librechat_ops import sync_agents


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ready, detail = sync_agents(ctx.log)
        if not ready:
            ctx.fail("chat_policy", "chat_sync_failed", detail)
