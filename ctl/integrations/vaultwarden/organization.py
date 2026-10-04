"""Organization bootstrap only: bw cannot create an organization.

The returned key is never used for routine item/collection encryption; bw
owns all those operations. The service account's password stays in memory.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ctl.integrations.vaultwarden.bootstrap import (
    SymmetricKey,
    VaultError,
    _b64,
    decrypt_bytes,
    derive_master_key,
    encrypt,
    master_password_hash,
    stretch,
)


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key[:1].lower() + key[1:]: _normalize(item) for key, item in value.items()}
    return value


def organization_bootstrap(base_url: str, email: str, password: str, name: str = "Mu3Lab") -> str:
    """Create the missing organization using the official web-vault protocol."""
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:

        def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            try:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return _normalize(response.json())
            except (httpx.HTTPError, ValueError):
                raise VaultError(
                    "The vault could not create Mu3Lab's shared organization.", "organization_bootstrap"
                ) from None

        kdf = request("POST", "/identity/accounts/prelogin", json={"email": email})
        master = derive_master_key(password, email, kdf)
        login = request(
            "POST",
            "/identity/connect/token",
            data={
                "grant_type": "password",
                "username": email,
                "password": master_password_hash(master, password),
                "scope": "api offline_access",
                "client_id": "cli",
                "deviceType": "25",
                "deviceIdentifier": str(uuid.uuid4()),
                "deviceName": "Mu3Lab organization bootstrap",
            },
        )
        user_key = SymmetricKey.from_bytes(decrypt_bytes(login["key"], stretch(master)))
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        profile = request("GET", "/api/sync", params={"excludeDomains": "true"})["profile"]
        for org in profile.get("organizations") or []:
            if org.get("name") == name and org.get("type") == 0:
                return str(org["id"])
        private = serialization.load_der_private_key(decrypt_bytes(profile["privateKey"], user_key), password=None)
        if not isinstance(private, rsa.RSAPrivateKey):
            raise VaultError("Mu3Lab's vault key is unsupported.")
        org_key = SymmetricKey.from_bytes(os.urandom(64))
        org_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = org_private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        private_der = org_private.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        sealed = private.public_key().encrypt(
            org_key.enc + org_key.mac,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
        )
        result = request(
            "POST",
            "/api/organizations",
            json={
                "name": name,
                "billingEmail": email,
                "planType": 0,
                "key": "4." + _b64(sealed),
                "collectionName": encrypt("Shared", org_key),
                "keys": {
                    "publicKey": base64.b64encode(public_der).decode(),
                    "encryptedPrivateKey": encrypt(private_der, org_key),
                },
            },
        )
        return str(result["id"])


def invite_member(base_url: str, owner_email: str, password: str, org_id: str, email: str) -> None:
    """The CLI and admin API cannot invite an organization member.

    This supported Vaultwarden API exception was approved by the owner.
    Its authentication is used only for this unsupported CLI operation.
    """
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        try:
            response = client.post("/identity/accounts/prelogin", json={"email": owner_email})
            response.raise_for_status()
            master = derive_master_key(password, owner_email, response.json())
            response = client.post(
                "/identity/connect/token",
                data={
                    "grant_type": "password",
                    "username": owner_email,
                    "password": master_password_hash(master, password),
                    "scope": "api offline_access",
                    "client_id": "cli",
                    "deviceType": "25",
                    "deviceIdentifier": str(uuid.uuid4()),
                    "deviceName": "Mu3Lab organization invitation",
                },
            )
            response.raise_for_status()
            token = response.json()["access_token"]
            response = client.post(
                f"/api/organizations/{org_id}/users/invite",
                headers={"Authorization": "Bearer " + token},
                json={
                    "emails": [email.lower()],
                    "type": 2,
                    "accessAll": False,
                    "collections": [],
                    "groups": [],
                    "permissions": {},
                },
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError, KeyError):
            raise VaultError(
                "The vault could not invite this person to Mu3Lab's organization.", "organization_invite"
            ) from None
