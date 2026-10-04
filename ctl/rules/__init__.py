"""Shared app behaviors, written once and switched on per app in its manifest.

An app lists the rules it follows under ``rules:`` with its own settings
(``with:``); an app may follow several. Each rule is a small class with a
``Params`` model and any of these hook points, called by the install engine
in this order:

``prepare_env``  while the project's private settings are generated
``before_start`` checks that must pass before any container starts
``plan_start``   how the containers start the first time
``after_start``  setup steps once the containers run
``after_healthy`` setup steps once the app answers its health check
``periodic``     a worker check that runs every minute for installed apps

No rule names a specific app; app-specific scripts and queries live in the
app's folder and reach the rule as settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ctl.engine.hooks import HookContext, StartPlan
from ctl.workflow_secrets import JobIdentity

if TYPE_CHECKING:
    from ctl.manifest.catalog import App, Catalog
    from ctl.manifest.models import AppManifest


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


RuleParams = Params


class Rule:
    name: ClassVar[str]
    Params: ClassVar[type[Params]] = Params
    summary: ClassVar[str] = ""

    def __init__(self, params: RuleParams) -> None:
        self.params = params

    @property
    def needs_owner(self) -> bool:
        """True when the rule needs the installing person's verified Authentik identity."""
        return False

    def prepare_env(self, app: App, env: dict[str, str], owner: JobIdentity | None) -> None:
        return None

    def before_start(self, ctx: HookContext) -> None:
        return None

    def plan_start(self, ctx: HookContext, plan: StartPlan) -> None:
        return None

    def after_start(self, ctx: HookContext) -> None:
        return None

    def after_healthy(self, ctx: HookContext) -> None:
        return None

    def periodic(self, ctx: HookContext) -> str:
        """Return a line for the worker log when something changed, else ""."""
        return ""


_RULES: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    _RULES[cls.name] = cls
    return cls


class RuleError(ValueError):
    """A manifest names an unknown rule or gives it invalid settings."""


def rules_for(manifest: AppManifest) -> list[Rule]:
    _load_all()
    result: list[Rule] = []
    for ref in manifest.rules:
        cls = _RULES.get(ref.rule)
        if cls is None:
            raise RuleError(f"{manifest.id}: unknown rule {ref.rule!r}")
        try:
            result.append(cls(cls.Params.model_validate(ref.with_)))
        except ValidationError as exc:
            raise RuleError(f"{manifest.id}: rule {ref.rule}: {exc}") from exc
    return result


def check(catalog: Catalog) -> None:
    """Raise RuleError when any app's rules are unknown or misconfigured."""
    for app in catalog.apps:
        for rule in rules_for(app.manifest):
            check_files = getattr(rule, "check_files", None)
            if check_files:
                check_files(app)


def names() -> list[str]:
    _load_all()
    return sorted(_RULES)


def _load_all() -> None:
    # Importing each module registers its rule.
    from ctl.rules import (  # noqa: F401
        container_script,
        first_admin_from_env,
        hide_model_providers,
        initial_owner_guard,
        needs_embedding_model,
        password_login_off_after_setup,
        provider_routing,
        staged_first_start,
    )
