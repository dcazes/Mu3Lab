"""The recovery sheet: what an administrator needs to open the backups on a new computer.

Backups are encrypted with one password that Mu3Lab keeps in its encrypted
store on this computer. If the computer is lost, that copy is lost with it, so
the password must also live somewhere else: on a printed sheet, and in every
administrator's vault (see ``vault_sync``). Bitwarden keeps an offline copy of
the vault on each signed-in device, so that copy survives this server too.

Each opening of the sheet is recorded (who and when, never the password), so
the dashboard can say whether anyone has saved it yet.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ctl import __version__, backups
from ctl.runtime import RuntimePaths
from ctl.store import records

SCOPE = ("backup", "recovery-sheet")
VAULT_ITEM_NAME = "Mu3Lab backup password"


def password_check(password: str) -> str:
    """A short code that confirms a retyped password is the right one, without revealing it."""
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()[:8].upper()
    return f"{digest[:4]}-{digest[4:]}"


def repository(paths: RuntimePaths) -> str:
    return str(paths.backups / "kopia")


def vault_notes(host: str, paths: RuntimePaths) -> str:
    where = f" on {host}" if host else ""
    return (
        f"Opens every Mu3Lab backup, kept in {repository(paths)}{where}. Without it nobody can read the backups, "
        "you included. To rebuild on a new computer, follow 'Rebuilding on a new computer' in "
        "docs/backup-recovery.md. Keep the printed recovery sheet too: this vault runs on the same server."
    )


def contents(host: str, actor: str, paths: RuntimePaths) -> dict[str, Any]:
    """The sheet itself. Creates the backup password if no backup has needed it yet."""
    password = backups.repository_password(paths)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    records.put(*SCOPE, {"opened_at": now, "opened_by": actor}, paths)
    return {
        "server": host,
        "generated_at": now,
        "version": __version__,
        "password": password,
        "password_check": password_check(password),
        "repository": repository(paths),
        "kopia_image": backups.KOPIA_IMAGE,
        # Copies to another computer come with the SFTP step; until then there are none.
        "copies": [],
    }


def status(paths: RuntimePaths) -> dict[str, str] | None:
    """When the sheet was last opened and by whom, or None if never."""
    state = records.get(*SCOPE, paths)
    if not state.get("opened_at"):
        return None
    return {"opened_at": str(state["opened_at"]), "opened_by": str(state.get("opened_by") or "")}
