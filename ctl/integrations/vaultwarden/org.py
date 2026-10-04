"""Shared organization operations through the official Bitwarden CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ctl.integrations.vaultwarden import admin
from ctl.integrations.vaultwarden.cli import VaultSession
from ctl.integrations.vaultwarden.organization import invite_member, organization_bootstrap
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

STATUS_ACCEPTED, STATUS_CONFIRMED = 1, 2


@dataclass
class Member:
    id: str
    user_id: str
    email: str
    status: int
    collections: list[str] = field(default_factory=list)


@dataclass
class OrgLogin:
    id: str
    name: str
    username: str
    uris: list[str]
    fields: dict[str, str]
    collection_ids: list[str]
    raw: dict[str, Any] = field(repr=False)


class OrgSession(VaultSession):
    def __enter__(self) -> OrgSession:
        return self

    def login(self, email: str, password: str, **kwargs: Any) -> None:
        super().login(email, password, **kwargs)
        self._bootstrap_email, self._bootstrap_password = email, password

    def close(self) -> None:
        self._bootstrap_password = ""
        super().close()

    def ensure_organization(self, billing_email: str) -> tuple[str, None]:
        self.run("sync", raw=True)
        for org in self.run("list", "organizations"):
            if org["name"] == "Mu3Lab" and org.get("type") == 0:
                return org["id"], None
        org_id = organization_bootstrap(self.base_url, self._bootstrap_email, self._bootstrap_password)
        self.run("sync", raw=True)
        return org_id, None

    def members(self, org_id: str) -> list[Member]:
        return [
            Member(
                m["id"],
                m.get("userId") or "",
                m["email"].lower(),
                m["status"],
                [c["id"] for c in m.get("collections") or []],
            )
            for m in self.run("list", "org-members", "--organizationid", org_id)
        ]

    def invite(self, org_id: str, email: str) -> None:
        env = read_runtime_env(RuntimePaths().projects / by_capability("password_store").id / ".env")
        admin.invite(self.base_url, env["MU3LAB_ADMIN_PASSWORD"], email)
        invite_member(self.base_url, self._bootstrap_email, self._bootstrap_password, org_id, email)

    def confirm(self, org_id: str, member: Member, org_key: None) -> None:
        self.run("confirm", "org-member", member.id, "--organizationid", org_id, raw=True)

    def collections(self, org_id: str, org_key: None) -> dict[str, str]:
        return {c["id"]: c["name"] for c in self.run("list", "org-collections", "--organizationid", org_id)}

    @staticmethod
    def _collection(name: str, members: list[str]) -> dict[str, Any]:
        return {
            "name": name,
            "groups": [],
            "users": [{"id": m, "readOnly": False, "hidePasswords": False, "manage": False} for m in members],
        }

    def create_collection(self, org_id: str, org_key: None, name: str, member_ids: list[str]) -> str:
        return str(
            self.run(
                "create",
                "org-collection",
                "--organizationid",
                org_id,
                body={**self._collection(name, member_ids), "organizationId": org_id},
            )["id"]
        )

    def rename_collection(
        self, org_id: str, org_key: None, collection_id: str, name: str, member_ids: list[str]
    ) -> None:
        self.run(
            "edit",
            "org-collection",
            collection_id,
            "--organizationid",
            org_id,
            body={**self._collection(name, member_ids), "organizationId": org_id},
        )

    def logins(self, org_id: str, org_key: None) -> list[OrgLogin]:
        self.run("sync", raw=True)
        result = []
        for item in self.run("list", "items", "--organizationid", org_id):
            if item.get("type") != 1:
                continue
            login = item.get("login") or {}
            result.append(
                OrgLogin(
                    item["id"],
                    item["name"],
                    login.get("username") or "",
                    [u["uri"] for u in login.get("uris") or [] if u.get("uri")],
                    {f["name"]: f.get("value") or "" for f in item.get("fields") or []},
                    item.get("collectionIds") or [],
                    item,
                )
            )
        return result

    def create_login(self, org_id: str, org_key: None, collection_id: str, **item: Any) -> str:  # type: ignore[override]
        return str(
            self.run(
                "create", "item", body=self.item_body(**item, organizationId=org_id, collectionIds=[collection_id])
            )["id"]
        )

    def update_login(self, existing: OrgLogin, org_id: str, org_key: None, **item: Any) -> None:
        self.run(
            "edit",
            "item",
            existing.id,
            body=self.item_body(**item, organizationId=org_id, collectionIds=existing.collection_ids, id=existing.id),
        )

    def delete_login(self, login_id: str) -> None:
        self.run("delete", "item", login_id, raw=True)
