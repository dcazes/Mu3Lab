"""Open Mu3Lab's backup repository read-only, for an app that browses backups.

The app is given the repository's password and where the backups are. The
repository is created first if no backup has been made yet, with Mu3Lab's
own policy (``backups.ensure_repository``), so the app always has one to open.
The app mounts it read-only and must connect read-only too: Mu3Lab alone
creates, prunes and maintains backups.
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


class ReadsBackupsParams(Params):
    password_env: str = "KOPIA_PASSWORD"
    backups_env: str = "MU3LAB_BACKUPS_ROOT"


@register
class ReadsBackups(Rule):
    name = "reads_backups"
    Params = ReadsBackupsParams
    summary = "Gives the app read-only access to Mu3Lab's backups, creating the repository if needed."
    params: ReadsBackupsParams

    def prepare_env(self, app: App, env: dict[str, str], owner: JobIdentity | None) -> None:
        paths = RuntimePaths()
        env[self.params.password_env] = backups.repository_password(paths)
        env[self.params.backups_env] = str(paths.backups)

    def before_start(self, ctx: HookContext) -> None:
        paths = ctx.facts.paths if ctx.facts else RuntimePaths()
        try:
            backups.ensure_repository(ctx.log, paths)
        except backups.BackupError as exc:
            ctx.fail("before_start", "backup_repository_unavailable", str(exc))
        (paths.backups / "restores").mkdir(mode=0o750, exist_ok=True)
