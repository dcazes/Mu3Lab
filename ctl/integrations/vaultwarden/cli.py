"""Official standalone Bitwarden CLI, isolated and erased after each job."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ctl.integrations.vaultwarden.bootstrap import VaultError
from ctl.integrations.vaultwarden.loopback_tls import LoopbackTLS

ROOT = Path(__file__).resolve().parents[3]
MATCH_HOST = 1


@dataclass
class Folder:
    id: str
    name: str


@dataclass
class Login:
    id: str
    name: str
    folder_id: str | None
    username: str
    uris: list[str]
    fields: dict[str, str]
    raw: dict[str, Any] = field(repr=False)

    @property
    def mu3lab_id(self) -> str:
        return self.fields.get("mu3lab_id", "")


def login_view(item: dict[str, Any]) -> Login:
    login = item.get("login") or {}
    return Login(
        str(item["id"]),
        str(item["name"]),
        item.get("folderId"),
        login.get("username") or "",
        [uri["uri"] for uri in login.get("uris") or [] if uri.get("uri")],
        {f["name"]: f.get("value") or "" for f in item.get("fields") or []},
        item,
    )


class VaultSession:
    def __init__(self, base_url: str, *, binary: Path | None = None, timeout: float = 90) -> None:
        self.base_url = base_url.rstrip("/")
        self.binary = binary or ROOT / ".tools" / "bin" / "bw"
        self.timeout = timeout
        self.bridge: LoopbackTLS | None = None
        self.temporary = tempfile.TemporaryDirectory(prefix="mu3lab-bw-")
        self.env = {**os.environ, "BITWARDENCLI_APPDATA_DIR": self.temporary.name, "BW_NOINTERACTION": "true"}
        for name in ("BW_SESSION", "BW_PASSWORD", "BW_CLIENTID", "BW_CLIENTSECRET"):
            self.env.pop(name, None)

    def __enter__(self) -> VaultSession:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        self.env.clear()
        self.temporary.cleanup()

    def run(self, *args: str, body: Any = None, raw: bool = False) -> Any:
        data = base64.b64encode(json.dumps(body).encode()).decode() if body is not None else None
        try:
            proc = subprocess.run(
                [str(self.binary), *args, "--raw"],
                input=data,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise VaultError(
                "The Bitwarden tool is unavailable. Check its installation and try again.", "cli_unavailable"
            ) from None
        if proc.returncode:
            if args[0] == "login":
                code = (
                    "two_factor_required"
                    if "two-step" in proc.stderr.lower() or "two-factor" in proc.stderr.lower()
                    else "invalid_credentials"
                )
                message = (
                    "Enter the current code from your authenticator app."
                    if code == "two_factor_required"
                    else "Vaultwarden did not accept that email and master password."
                )
                raise VaultError(message, code)
            # Output can contain vault items, passwords or sessions. Never expose it.
            raise VaultError(f"Bitwarden could not complete {args[0]}. Check the account and try again.", "cli_failed")
        if raw:
            return proc.stdout.strip()
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            raise VaultError("Bitwarden returned an unexpected response.", "cli_response") from None

    def login(self, email: str, password: str, *, totp: str = "", two_factor_provider: int = 0) -> None:
        try:
            cli_url = self.base_url
            if urlsplit(self.base_url).scheme == "http":
                self.bridge = LoopbackTLS(self.base_url, Path(self.temporary.name))
                cli_url = self.bridge.origin
                self.env["NODE_EXTRA_CA_CERTS"] = str(self.bridge.certificate)
            self.run("config", "server", cli_url, raw=True)
            self.env["BW_PASSWORD"] = password
            args = ["login", email, "--passwordenv", "BW_PASSWORD"]
            if totp:
                args.extend(["--method", str(two_factor_provider), "--code", totp])
            self.env["BW_SESSION"] = self.run(*args, raw=True)
        except BaseException:
            self.close()
            raise
        finally:
            self.env.pop("BW_PASSWORD", None)

    def sync(self) -> tuple[list[Folder], list[Login]]:
        self.run("sync", raw=True)
        folders = [Folder(f["id"], f["name"]) for f in self.run("list", "folders")]
        logins = [
            login_view(i) for i in self.run("list", "items") if i.get("type") == 1 and not i.get("organizationId")
        ]
        return folders, logins

    def create_folder(self, name: str) -> Folder:
        item = self.run("create", "folder", body={"name": name})
        return Folder(item["id"], name)

    @staticmethod
    def item_body(
        *,
        name: str,
        username: str,
        password: str,
        uris: list[tuple[str, int | None]],
        notes: str,
        fields: dict[str, str],
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "type": 1,
            "name": name,
            "notes": notes,
            "favorite": False,
            "reprompt": 0,
            "login": {
                "username": username,
                "password": password,
                "totp": None,
                "uris": [{"uri": uri, "match": match} for uri, match in uris],
            },
            "fields": [{"name": k, "value": v, "type": 0} for k, v in fields.items()],
            **extra,
        }

    def create_login(self, **item: Any) -> str:
        folder_id = item.pop("folder_id", None)
        return str(self.run("create", "item", body=self.item_body(**item, folderId=folder_id))["id"])

    def decrypt_password(self, login: Login) -> str:
        return str((login.raw.get("login") or {}).get("password") or "")

    def replace_password(self, login: Login, password: str) -> None:
        body = dict(login.raw)
        body["login"] = {**body.get("login", {}), "password": password}
        if self.decrypt_password(login):
            body["passwordHistory"] = [
                {"password": self.decrypt_password(login), "lastUsedDate": datetime.now(UTC).isoformat()},
                *body.get("passwordHistory", []),
            ][:5]
        self.run("edit", "item", login.id, body=body)
