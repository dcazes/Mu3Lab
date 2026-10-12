"""Give each person an account and a generated password in an app without single sign-on.

Unlike ``per_person_accounts`` (an app that trusts Mu3Lab's identity header),
these apps also need a password: Unsloth Studio for its own web app, Navidrome
for phone apps. The app's ``hooks.py`` (``sync_people``) creates what is
missing through the app's own API and keeps each password in
``ctl.store.person_logins``; Mu3Lab then saves it to that person's vault.
It runs once the app is healthy, and again whenever the household changes.
A person who leaves the audience (deactivated, or no longer an administrator
for ``audience: operators``) has their account closed, so a saved password
stops working too.
"""

from __future__ import annotations

from typing import Any, Literal

from ctl.control_state import ControlState
from ctl.engine.hooks import HookContext, StepFailed, load_app_hooks
from ctl.people import PeopleError, list_people
from ctl.rules import Params, Rule, register
from ctl.rules.per_person_accounts import digest

# Kept in the app's private settings so a restart does not rerun an unchanged sync.
DIGEST_ENV = "MU3LAB_PEOPLE_LOGINS_SYNCED"


class PerPersonLoginsParams(Params):
    audience: Literal["household", "operators"] = "household"
    purpose: str = "Create an account for each household member"


def everyone(audience: str, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every person the app has heard of, marked ``member`` when they should have a working account."""
    return sorted(
        (
            {
                "username": str(person["username"]),
                "name": str(person.get("name") or person["username"]),
                "email": str(person.get("email") or ""),
                "admin": person.get("role") == "admin",
                "member": bool(person.get("active", True))
                and (audience == "household" or person.get("role") == "admin"),
            }
            for person in people
            if person.get("username")
        ),
        key=lambda person: person["username"],
    )


@register
class PerPersonLogins(Rule):
    name = "per_person_logins"
    Params = PerPersonLoginsParams
    summary = "Creates each person's app account with a generated password and saves it to their vault."
    params: PerPersonLoginsParams

    def _sync(self, ctx: HookContext, people: list[dict[str, Any]]) -> str:
        """An empty string when the accounts match, else why they do not."""
        try:
            load_app_hooks(ctx.app).sync_people(ctx, people)
        except StepFailed as exc:
            return str(exc)
        except (OSError, ValueError, RuntimeError) as exc:
            return str(exc) or type(exc).__name__
        ctx.set_env({DIGEST_ENV: digest(people)})
        return ""

    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", self.params.purpose + ".")
        try:
            people = everyone(self.params.audience, list_people())
        except PeopleError as exc:
            ctx.fail("configure_application", "people_unavailable", f"The household list could not be read: {exc}")
            return
        problem = self._sync(ctx, people)
        if problem:
            ctx.fail(
                "configure_application",
                "application_configuration_failed",
                f"{ctx.app.manifest.name} could not create accounts: {problem}",
            )

    def periodic(self, ctx: HookContext) -> str:
        state = ControlState.runtime(ctx.facts.paths) if ctx.facts else ControlState.runtime()
        installation = state.installation(ctx.app.id) if state else None
        if not installation or installation["state"] != "running":
            return ""  # a stopped app catches up once it runs again
        try:
            people = everyone(self.params.audience, list_people())
        except PeopleError:
            return ""  # Authentik is briefly unavailable; the next minute retries.
        if ctx.env().get(DIGEST_ENV) == digest(people):
            return ""
        problem = self._sync(ctx, people)
        if problem:
            ctx.log(f"{ctx.app.manifest.name} accounts were not updated: {problem}")
            return ""
        return f"{ctx.app.manifest.name} accounts now match the household."
