"""Hide every built-in AI provider a chat app ships, except the one Mu3Lab routes.

The provider list is data in the app's folder (one provider id per line).
Each listed provider gets ``<ID>_MODEL_LIST=-all`` and ``ENABLED_<ID>=0``; the
kept provider is enabled with only Mu3Lab's own model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ctl.rules import Params, Rule, RuleError, register
from ctl.store.workflows import JobIdentity

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class HideProvidersParams(Params):
    providers_file: str  # one provider id per line, in the app folder
    keep: str = "openai"
    keep_models: str = "-all,+mu3lab-chat"


@register
class HideModelProviders(Rule):
    name = "hide_model_providers"
    Params = HideProvidersParams
    summary = "Hides every built-in AI provider except the one Mu3Lab routes through LiteLLM."
    params: HideProvidersParams

    def check_files(self, app: App) -> None:
        if not (app.folder / self.params.providers_file).is_file():
            raise RuleError(f"{app.id}: hide_model_providers needs {self.params.providers_file}")

    def prepare_env(self, app: App, env: dict[str, str], owner: JobIdentity | None) -> None:
        lines = (app.folder / self.params.providers_file).read_text(encoding="utf-8").splitlines()
        for provider in (line.strip() for line in lines if line.strip() and not line.startswith("#")):
            env[f"{provider.upper()}_MODEL_LIST"] = "-all"
            env[f"ENABLED_{provider.upper()}"] = "0"
        keep = self.params.keep.upper()
        env[f"ENABLED_{keep}"] = "1"
        env[f"{keep}_MODEL_LIST"] = self.params.keep_models
