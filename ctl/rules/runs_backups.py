"""Run Mu3Lab's backup service: Kopia's server, which makes, schedules and keeps every backup.

The app is given the repository's password, where the backups are, and where
its own cache and logs live. The repository is created first if no backup has
been made yet, with Mu3Lab's policy (``backups.ensure_repository``), so the
server always has one to open. Which folders it backs up, its schedule and
its actions are kept in step by the worker (``ctl.backup_service``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ctl import backups
from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, register
from ctl.runtime import RuntimePaths
from ctl.store.workflows import JobIdentity

if TYPE_CHECKING:
    from ctl.manifest.catalog import App


class RunsBackupsParams(Params):
    password_env: str = "KOPIA_PASSWORD"
    backups_env: str = "MU3LAB_BACKUPS_ROOT"
    state_env: str = "MU3LAB_STATE_ROOT"


@register
class RunsBackups(Rule):
    name = "runs_backups"
    Params = RunsBackupsParams
    summary = "Runs Kopia's server as Mu3Lab's backup service, creating the repository if needed."
    params: RunsBackupsParams

    def prepare_env(
        self, app: App, env: dict[str, str], owner: JobIdentity | None, paths: RuntimePaths | None = None
    ) -> None:
        # The install's own runtime, never /srv/mu3lab by default: the Compose check renders into a temporary one.
        paths = paths or RuntimePaths()
        env[self.params.password_env] = backups.repository_password(paths)
        env[self.params.backups_env] = str(paths.backups)
        env[self.params.state_env] = str(paths.state)

    def before_start(self, ctx: HookContext) -> None:
        from ctl import platform_backup

        paths = ctx.facts.paths if ctx.facts else RuntimePaths()
        try:
            backups.ensure_repository(ctx.log, paths)
        except backups.BackupError as exc:
            ctx.fail("before_start", "backup_repository_unavailable", str(exc))
        (paths.backups / "restores").mkdir(mode=0o750, exist_ok=True)
        (paths.state / "kopia-server").mkdir(mode=0o700, exist_ok=True)
        platform_backup.export_folder(paths)
