"""What a rule or an app's own ``hooks.py`` may see and do during an install.

Install steps call shared rules (``ctl.rules``) and, for the few things only
one app does, that app's ``apps/<id>/hooks.py``. Both receive a ``HookContext``:
the app, its project, its Compose commands, the installing owner, a logger,
and ``fail()`` to stop the install with a plain-language reason.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ctl.engine.compose import Compose, ScriptResult
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store.secrets import SecretStore
from ctl.store.workflows import JobIdentity

if TYPE_CHECKING:
    from ctl.engine.project import Facts
    from ctl.manifest.catalog import App

Log = Callable[[str], None]


class StepFailed(Exception):
    """Stops an install at one stage with a stable code and a message for people."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


@dataclass
class StartPlan:
    """How the app's containers start the first time in this install.

    Rules adjust it: start only some containers first, add a first-start
    override file and its private values, or skip Compose's health wait until
    a setup step has run. After the start hooks, the engine starts everything
    else and drops first-start overrides.
    """

    services: list[str] = field(default_factory=list)
    extra_files: list[Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    wait: bool = True

    @property
    def needs_final_start(self) -> bool:
        return bool(self.services or self.extra_files or self.env or not self.wait)


@dataclass
class HookContext:
    app: App
    project: Path
    compose: Compose
    log: Log
    stage: Callable[[str, str], None]
    owner: JobIdentity | None = None
    facts: Facts | None = None
    # Rewrite the project's settings and template files from the manifest.
    rerender: Callable[[], None] = lambda: None
    # Re-register this app's sign-in with Authentik after its settings change.
    reregister_sign_in: Callable[[], None] = lambda: None
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def env_path(self) -> Path:
        return self.project / ".env"

    def env(self) -> dict[str, str]:
        return read_runtime_env(self.env_path)

    def set_env(self, values: dict[str, str]) -> bool:
        """Merge values into the project's private settings; True when anything changed."""
        paths = self.facts.paths if self.facts else RuntimePaths(self.project.parent.parent)
        store = SecretStore(paths)
        for name, value in values.items():
            store.put("app:" + self.app.id, "env:" + name, value)
        current = self.env()
        merged = current | values
        if merged == current:
            return False
        temporary = self.env_path.with_name(".env.tmp")
        temporary.write_text(runtime_env_text(merged), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.env_path)
        return True

    def fail(self, stage: str, code: str, message: str) -> None:
        raise StepFailed(stage, code, message)

    def run_script(
        self,
        service: str,
        script: str,
        interpreter: Sequence[str],
        *,
        args: Sequence[str] = (),
        timeout: int = 300,
        folder: Path | None = None,
    ) -> ScriptResult:
        """Run a script kept in the app's folder (or ``folder``) inside one of its containers."""
        source = (folder or self.app.folder) / script
        return self.compose.run_script(
            service, interpreter, source.read_text(encoding="utf-8"), self.log, args=args, timeout=timeout
        )


class AppHooks:
    """Steps only one app needs. Every method is optional and does nothing by default."""

    def bootstrap_account(self, ctx: HookContext) -> str:
        """Create the owner's account through the app's own API; return a sentence for the job log."""
        return ""

    def after_healthy(self, ctx: HookContext) -> None:
        """Finish app-specific setup once its health check passes."""
        return None


def load_app_hooks(app: App) -> AppHooks:
    """Import ``apps/<id>/hooks.py`` by path; an app without one gets the empty defaults."""
    path = app.folder / "hooks.py"
    if not path.is_file():
        return AppHooks()
    spec = importlib.util.spec_from_file_location(f"mu3lab_app_hooks_{app.id.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hooks = getattr(module, "Hooks", None)
    if not isinstance(hooks, type) or not issubclass(hooks, AppHooks):
        raise ImportError(f"{path} must define class Hooks(AppHooks)")
    return hooks()
