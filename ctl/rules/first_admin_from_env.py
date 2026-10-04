"""Create the app's first administrator from first-start settings, then remove them.

Apps such as Paperless-ngx, AdventureLog and Nextcloud create an administrator
when they first start with ``*_ADMIN_*`` settings. On a fresh install Mu3Lab
layers the app's bootstrap override (which maps ``MU3LAB_BOOTSTRAP_*`` to the
app's own names) with the owner's Authentik username and a random password,
checks the account exists, and the engine then restarts the app without those
settings. The password is never stored: these apps sign in only through
Authentik.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ctl.engine.hooks import HookContext, StartPlan
from ctl.engine.project import token
from ctl.rules import Params, Rule, RuleError, register

if TYPE_CHECKING:
    from ctl.manifest.catalog import App

DJANGO_CHECK = Path(__file__).with_name("scripts") / "django_superuser.py"


class DjangoSuperuser(Params):
    service: str


class ScriptCheck(Params):
    service: str
    script: str
    interpreter: tuple[str, ...] = Field(min_length=1)


class Verify(Params):
    django_superuser: DjangoSuperuser | None = None
    script: ScriptCheck | None = None


class FirstAdminParams(Params):
    override: str  # compose override in the app folder that maps MU3LAB_BOOTSTRAP_* to the app's settings
    fresh_when_empty: str  # data sub-folder that is empty until the app's database exists
    username: Literal["sanitized", "verbatim"] = "sanitized"
    verify: Verify | None = None


def account_username(owner_username: str, email: str) -> str:
    candidate = owner_username or email.split("@", 1)[0]
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:64]
    return value or "mu3lab-admin"


@register
class FirstAdminFromEnv(Rule):
    name = "first_admin_from_env"
    Params = FirstAdminParams
    summary = "Creates the first administrator from first-start settings, then removes them."
    params: FirstAdminParams

    @property
    def needs_owner(self) -> bool:
        return True

    def check_files(self, app: App) -> None:
        if not (app.folder / self.params.override).is_file():
            raise RuleError(f"{app.id}: first_admin_from_env needs {self.params.override}")
        script = self.params.verify.script if self.params.verify else None
        if script and not (app.folder / script.script).is_file():
            raise RuleError(f"{app.id}: first_admin_from_env needs {script.script}")

    def _fresh(self, ctx: HookContext) -> bool:
        assert ctx.facts is not None
        folder = ctx.facts.paths.data / ctx.app.manifest.data_folder / self.params.fresh_when_empty
        try:
            return not folder.exists() or not any(folder.iterdir())
        except OSError:
            return False

    def plan_start(self, ctx: HookContext, plan: StartPlan) -> None:
        if not self._fresh(ctx):
            ctx.stage("account_bootstrap", "Existing administrator kept; its data is reused.")
            return
        if ctx.owner is None:
            ctx.fail("account_preflight", "identity_email_missing", "A verified Authentik email is required.")
            return
        owner = ctx.owner
        username = (
            owner["username"]
            if self.params.username == "verbatim"
            else account_username(owner["username"], owner["email"])
        )
        plan.extra_files.append(ctx.project / self.params.override)
        plan.env.update(
            {
                "MU3LAB_BOOTSTRAP_USERNAME": username,
                "MU3LAB_BOOTSTRAP_EMAIL": owner["email"],
                "MU3LAB_BOOTSTRAP_PASSWORD": token(32),
            }
        )
        ctx.notes["first_admin"] = username
        ctx.stage("account_bootstrap", "Creating the initial application administrator.")

    def after_start(self, ctx: HookContext) -> None:
        username = ctx.notes.get("first_admin")
        verify = self.params.verify
        if not username or verify is None:
            return
        if verify.django_superuser:
            script = f"USERNAME = {json.dumps(username)}\n" + DJANGO_CHECK.read_text(encoding="utf-8")
            result = ctx.compose.run_script(
                verify.django_superuser.service, ["python", "manage.py", "shell"], script, ctx.log, timeout=120
            )
        else:
            assert verify.script is not None
            result = ctx.run_script(
                verify.script.service, verify.script.script, verify.script.interpreter, args=[username]
            )
        if not result.ok:
            ctx.fail(
                "account_verification",
                "account_verification_failed",
                "The app started but did not confirm the generated administrator account. " + result.error,
            )
        ctx.stage("account_verified", "The initial administrator exists.")
