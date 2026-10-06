"""Give each household member an account in an app that cannot create one on first visit.

Some apps trust Mu3Lab's identity header but only for accounts that already
exist (Beaver Habits). A script kept in the app's folder receives the current
household as JSON and creates whatever is missing through the app's own user
service. It runs once the app is healthy, and again whenever the household
changes. Removed people are shut out by Authentik itself, so the script only
creates.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ctl.engine.hooks import HookContext
from ctl.people import PeopleError, list_people
from ctl.rules import Params, Rule, RuleError, register

if TYPE_CHECKING:
    from ctl.manifest.catalog import App

# Kept in the app's private settings so a restart does not rerun an unchanged sync.
DIGEST_ENV = "MU3LAB_PEOPLE_SYNCED"


class PerPersonAccountsParams(Params):
    service: str
    script: str
    interpreter: tuple[str, ...] = Field(min_length=1)
    expect: str
    audience: Literal["household", "operators"] = "household"
    purpose: str = "Create an account for each household member"
    timeout_seconds: int = Field(default=120, ge=10, le=900)


def household(audience: str, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The people the app serves, as the script receives them, in a stable order."""
    chosen = [person for person in people if audience == "household" or person.get("role") == "admin"]
    return sorted(
        (
            {
                "username": str(person["username"]),
                "name": str(person.get("name") or person["username"]),
                "email": str(person.get("email") or ""),
                "admin": person.get("role") == "admin",
                "active": bool(person.get("active", True)),
            }
            for person in chosen
        ),
        key=lambda person: person["username"],
    )


def digest(people: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(people, sort_keys=True).encode()).hexdigest()


@register
class PerPersonAccounts(Rule):
    name = "per_person_accounts"
    Params = PerPersonAccountsParams
    summary = "Creates an app account for each household member through the app's own user service."
    params: PerPersonAccountsParams

    def check_files(self, app: App) -> None:
        if not (app.folder / self.params.script).is_file():
            raise RuleError(f"{app.id}: per_person_accounts needs {self.params.script}")

    def _sync(self, ctx: HookContext, people: list[dict[str, Any]]) -> bool:
        result = ctx.run_script(
            self.params.service,
            self.params.script,
            self.params.interpreter,
            args=[json.dumps(people, separators=(",", ":"))],
            timeout=self.params.timeout_seconds,
        )
        if not result.ok or self.params.expect not in result.raw:
            ctx.log(f"{ctx.app.manifest.name} accounts were not updated: {result.error}".strip())
            return False
        ctx.set_env({DIGEST_ENV: digest(people)})
        return True

    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", self.params.purpose + ".")
        try:
            people = household(self.params.audience, list_people())
        except PeopleError as exc:
            ctx.fail("configure_application", "people_unavailable", f"The household list could not be read: {exc}")
            return
        if not self._sync(ctx, people):
            ctx.fail(
                "configure_application",
                "application_configuration_failed",
                f"{ctx.app.manifest.name} could not create household accounts.",
            )

    def periodic(self, ctx: HookContext) -> str:
        try:
            people = household(self.params.audience, list_people())
        except PeopleError:
            return ""  # Authentik is briefly unavailable; the next minute retries.
        if ctx.env().get(DIGEST_ENV) == digest(people):
            return ""
        if not self._sync(ctx, people):
            return ""
        return f"{ctx.app.manifest.name} accounts now match the household."
