"""Mu3Lab's own Vaultwarden account and the shared "Mu3Lab" organization.

Mu3Lab signs in to Vaultwarden as a dedicated service account that owns an
organization. Each person who uses Mu3Lab is a member of that organization
with one private collection ("Mu3Lab: <name>") only they can see. Mu3Lab writes
the logins it generates for a person into their collection, so their Bitwarden
apps fill them in, without ever asking for their master password.

Mu3Lab holds the organization's key, not anyone's personal key: it can read
and write the items it put in the organization and nothing in anyone's
personal vault. Adding a member needs only their public key (the
organization key is sealed to it with RSA-OAEP), as in Bitwarden's own
"confirm member" step.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa

from ctl.vaultwarden_api import (
    FIELD_TEXT,
    LOGIN_ITEM,
    SymmetricKey,
    VaultError,
    VaultSession,
    _b64,
    decrypt,
    decrypt_bytes,
    encrypt,
)

ORG_NAME = "Mu3Lab"
# Bitwarden organization roles and member states.
ROLE_OWNER, ROLE_USER = 0, 2
STATUS_INVITED, STATUS_ACCEPTED, STATUS_CONFIRMED = 0, 1, 2
# Bitwarden policy type "Activate auto-fill": turns on autofill-on-page-load for members.
POLICY_ACTIVATE_AUTOFILL = 11
_OAEP = asym_padding.OAEP(mgf=asym_padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


def rsa_encrypt(value: bytes, public_der: bytes) -> str:
    """A type-4 EncString (RSA-2048-OAEP-SHA1), as Bitwarden uses to share keys."""
    public_key = serialization.load_der_public_key(public_der)
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise VaultError("A member's vault key has an unsupported type.", "unsupported_key")
    return "4." + _b64(public_key.encrypt(value, _OAEP))


def rsa_decrypt(value: str, private_key: rsa.RSAPrivateKey) -> bytes:
    kind, _, body = value.partition(".")
    if kind not in {"3", "4"}:
        raise VaultError("The vault shared a key in an unsupported format.", "unsupported_key")
    return private_key.decrypt(base64.b64decode(body.split("|")[0]), _OAEP)


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
    """A signed-in session for Mu3Lab's service account."""

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._sync: dict[str, Any] | None = None
        self._private_key: rsa.RSAPrivateKey | None = None

    # ------------------------------------------------------------------ keys

    def refresh(self) -> dict[str, Any]:
        self._sync = self._api("GET", "/api/sync?excludeDomains=true")
        return self._sync

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        if self._private_key is None:
            profile = (self._sync or self.refresh()).get("profile") or {}
            der = decrypt_bytes(str(profile.get("privateKey") or ""), self.user_key)
            key = serialization.load_der_private_key(der, password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise VaultError("Mu3Lab's vault key has an unsupported type.", "unsupported_key")
            self._private_key = key
        return self._private_key

    def organization(self) -> tuple[str, SymmetricKey] | None:
        """The Mu3Lab organization this account owns, with its key, if it exists."""
        for org in (self._sync or self.refresh()).get("profile", {}).get("organizations") or []:
            if org.get("name") == ORG_NAME and int(org.get("type", -1)) == ROLE_OWNER and org.get("key"):
                key = SymmetricKey.from_bytes(rsa_decrypt(str(org["key"]), self.private_key))
                return str(org["id"]), key
        return None

    def ensure_organization(self, billing_email: str) -> tuple[str, SymmetricKey]:
        existing = self.organization()
        if existing:
            return existing
        org_key = SymmetricKey.from_bytes(os.urandom(64))
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        private_der = private.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        own_public = self.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        created = self._api(
            "POST",
            "/api/organizations",
            {
                "name": ORG_NAME,
                "billingEmail": billing_email,
                "planType": 0,
                # The owner's copy of the organization key, sealed to its own public key.
                "key": rsa_encrypt(org_key.enc + org_key.mac, own_public),
                "keys": {"publicKey": _b64(public_der), "encryptedPrivateKey": encrypt(private_der, org_key)},
                "collectionName": encrypt("Shared", org_key),
            },
        )
        self.refresh()
        return str(created["id"]), org_key

    # --------------------------------------------------------------- members

    def members(self, org_id: str) -> list[Member]:
        data = self._api("GET", f"/api/organizations/{org_id}/users?includeCollections=true")
        return [
            Member(
                id=str(item["id"]),
                user_id=str(item.get("userId") or ""),
                email=str(item.get("email") or "").lower(),
                status=int(item.get("status", STATUS_INVITED)),
                collections=[str(c["id"]) for c in item.get("collections") or []],
            )
            for item in data.get("data", [])
        ]

    def invite(self, org_id: str, email: str) -> None:
        self._api(
            "POST",
            f"/api/organizations/{org_id}/users/invite",
            {
                "emails": [email.lower()],
                "type": ROLE_USER,
                "accessAll": False,
                "collections": [],
                "groups": [],
                "permissions": {},
            },
        )

    def confirm(self, org_id: str, member: Member, org_key: SymmetricKey) -> None:
        """Seal the organization key to the member's public key, as an owner does."""
        public = self._api("GET", f"/api/users/{member.user_id}/public-key").get("publicKey")
        if not public:
            raise VaultError("That person's vault account has no public key yet.", "not_ready")
        self._api(
            "POST",
            f"/api/organizations/{org_id}/users/{member.id}/confirm",
            {"key": rsa_encrypt(org_key.enc + org_key.mac, base64.b64decode(str(public)))},
        )

    # ------------------------------------------------------------ collections

    def collections(self, org_id: str, org_key: SymmetricKey) -> dict[str, str]:
        """Collection id -> decrypted name."""
        data = self._api("GET", f"/api/organizations/{org_id}/collections")
        return {str(item["id"]): decrypt(item.get("name"), org_key) for item in data.get("data", [])}

    def create_collection(self, org_id: str, org_key: SymmetricKey, name: str, member_ids: list[str]) -> str:
        created = self._api(
            "POST",
            f"/api/organizations/{org_id}/collections",
            {
                "name": encrypt(name, org_key),
                "groups": [],
                "users": [
                    {"id": member_id, "readOnly": False, "hidePasswords": False, "manage": False}
                    for member_id in member_ids
                ],
            },
        )
        return str(created["id"])

    # -------------------------------------------------------------- policies

    def enable_autofill_on_page_load(self, org_id: str) -> bool:
        """Best effort: Bitwarden apps then fill Mu3Lab logins as soon as a login page opens."""
        try:
            self._api(
                "PUT",
                f"/api/organizations/{org_id}/policies/{POLICY_ACTIVATE_AUTOFILL}",
                {"type": POLICY_ACTIVATE_AUTOFILL, "enabled": True, "data": None},
            )
        except VaultError:
            return False
        return True

    # ----------------------------------------------------------------- items

    def logins(self, org_id: str, org_key: SymmetricKey) -> list[OrgLogin]:
        items: list[OrgLogin] = []
        for cipher in self.refresh().get("ciphers", []):
            if cipher.get("organizationId") != org_id or cipher.get("type") != LOGIN_ITEM or cipher.get("deletedDate"):
                continue
            key = (
                SymmetricKey.from_bytes(decrypt_bytes(cipher["key"], org_key)) if cipher.get("key") else org_key
            )
            login = cipher.get("login") or {}
            items.append(
                OrgLogin(
                    id=str(cipher["id"]),
                    name=decrypt(cipher.get("name"), key),
                    username=decrypt(login.get("username"), key),
                    uris=[decrypt(uri.get("uri"), key) for uri in login.get("uris") or [] if uri.get("uri")],
                    fields={
                        decrypt(item.get("name"), key): decrypt(item.get("value"), key)
                        for item in cipher.get("fields") or []
                        if item.get("name")
                    },
                    collection_ids=[str(c) for c in cipher.get("collectionIds") or []],
                    raw=cipher,
                )
            )
        return items

    def _login_body(
        self,
        org_id: str,
        org_key: SymmetricKey,
        *,
        name: str,
        username: str,
        password: str,
        uris: list[tuple[str, int | None]],
        notes: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        key = org_key
        return {
            "type": LOGIN_ITEM,
            "organizationId": org_id,
            "folderId": None,
            "name": encrypt(name, key),
            "notes": encrypt(notes, key) if notes else None,
            "favorite": False,
            "reprompt": 0,
            "login": {
                "username": encrypt(username, key) if username else None,
                "password": encrypt(password, key) if password else None,
                "uris": [{"uri": encrypt(uri, key), "match": match} for uri, match in uris],
                "totp": None,
            },
            "fields": [
                {"type": FIELD_TEXT, "name": encrypt(label, key), "value": encrypt(value, key)}
                for label, value in fields.items()
            ],
        }

    def create_login(  # type: ignore[override]
        self, org_id: str, org_key: SymmetricKey, collection_id: str, **item: Any
    ) -> str:
        body = {"cipher": self._login_body(org_id, org_key, **item), "collectionIds": [collection_id]}
        return str(self._api("POST", "/api/ciphers/create", body)["id"])

    def update_login(self, existing: OrgLogin, org_id: str, org_key: SymmetricKey, **item: Any) -> None:
        body = self._login_body(org_id, org_key, **item)
        old_password = (existing.raw.get("login") or {}).get("password")
        if old_password and old_password != body["login"]["password"]:
            body["passwordHistory"] = [{"password": old_password, "lastUsedDate": existing.raw.get("revisionDate")}]
        self._api("PUT", f"/api/ciphers/{existing.id}", body)

    def delete_login(self, login_id: str) -> None:
        self._api("DELETE", f"/api/ciphers/{login_id}")
