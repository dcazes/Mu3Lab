"""Preserve chat policy setup until the official API migration in Task E."""

from ctl.engine.hooks import AppHooks, HookContext
from ctl.lobehub_ops import reconcile


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("chat_policy", "Applying chat model and assistant settings.")
        ready, detail = reconcile(ctx.log)
        if not ready:
            ctx.fail("chat_policy", "lobehub_policy_failed", detail)
