"""A minimal Bitwarden-protocol client for seeding the owner's Vaultwarden.

Mu3Lab uses this once, on the owner's explicit request, to save generated
application credentials and provider sign-up entries into their personal
vault. The master password and every derived key live only in this process
for the duration of one request; nothing here writes to disk or logs.

Only the small, long-stable part of the protocol is implemented: prelogin,
password login, sync, folder creation, and personal login items encrypted as
type-2 EncStrings (AES-256-CBC + HMAC-SHA256). Organization items are never
read or written.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

KDF_PBKDF2 = 0
KDF_ARGON2ID = 1
LOGIN_ITEM = 1
FIELD_TEXT = 0
# Bitwarden UriMatchType. Host includes the port, which matters because every
# Mu3Lab service shares one tailnet hostname on different ports.
MATCH_HOST = 1
CLIENT_ID = "cli"
DEVICE_TYPE_LINUX_CLI = 25


class VaultError(Exception):
    """A user-presentable failure; never contains secret material."""

    def __init__(self, message: str, code: str = "vault_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SymmetricKey:
    enc: bytes
    mac: bytes

    @classmethod
    def from_bytes(cls, value: bytes) -> SymmetricKey:
        if len(value) != 64:
            raise VaultError("The vault returned an unsupported encryption key.", "unsupported_key")
        return cls(value[:32], value[32:])


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def encrypt(plaintext: str | bytes, key: SymmetricKey) -> str:
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key.enc), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.new(key.mac, iv + ciphertext, hashlib.sha256).digest()
    return f"2.{_b64(iv)}|{_b64(ciphertext)}|{_b64(mac)}"


def decrypt_bytes(value: str, key: SymmetricKey) -> bytes:
    kind, _, body = value.partition(".")
    parts = body.split("|")
    if kind != "2" or len(parts) != 3:
        raise VaultError("The vault contains an unsupported encryption format.", "unsupported_key")
    iv, ciphertext, mac = (base64.b64decode(part) for part in parts)
    expected = hmac.new(key.mac, iv + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise VaultError("A vault value failed its integrity check.", "integrity")
    decryptor = Cipher(algorithms.AES(key.enc), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decrypt(value: str | None, key: SymmetricKey) -> str:
    return decrypt_bytes(value, key).decode("utf-8") if value else ""


def _expand(master_key: bytes, info: bytes) -> bytes:
    return HKDFExpand(algorithm=hashes.SHA256(), length=32, info=info).derive(master_key)


def derive_master_key(password: str, email: str, kdf: dict[str, Any]) -> bytes:
    salt = email.strip().lower().encode("utf-8")
    secret = password.encode("utf-8")
    kind = int(kdf.get("kdf", KDF_PBKDF2))
    iterations = int(kdf.get("kdfIterations") or 0)
    if kind == KDF_PBKDF2:
        if iterations < 5000:
            raise VaultError("The vault reported an unsafe key-derivation setting.", "unsupported_kdf")
        return hashlib.pbkdf2_hmac("sha256", secret, salt, iterations, 32)
    if kind == KDF_ARGON2ID:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

        memory_mib = int(kdf.get("kdfMemory") or 0)
        parallelism = int(kdf.get("kdfParallelism") or 0)
        if iterations < 1 or memory_mib < 16 or parallelism < 1:
            raise VaultError("The vault reported an unsafe key-derivation setting.", "unsupported_kdf")
        return Argon2id(
            salt=hashlib.sha256(salt).digest(),
            length=32,
            iterations=iterations,
            lanes=parallelism,
            memory_cost=memory_mib * 1024,
        ).derive(secret)
    raise VaultError("The vault uses an unsupported key-derivation method.", "unsupported_kdf")


def stretch(master_key: bytes) -> SymmetricKey:
    return SymmetricKey(_expand(master_key, b"enc"), _expand(master_key, b"mac"))


def master_password_hash(master_key: bytes, password: str) -> str:
    return _b64(hashlib.pbkdf2_hmac("sha256", master_key, password.encode("utf-8"), 1, 32))


PBKDF2_ITERATIONS = 600_000


def register(base_url: str, email: str, password: str, name: str, *, client: httpx.Client | None = None) -> None:
    """Create a Vaultwarden account the way the official web vault does.

    Keys are generated here and only the encrypted user key, the RSA key pair
    (private key encrypted) and the master-password hash are sent. The
    password itself never leaves this process.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    email = email.strip().lower()
    if not email or not password:
        raise VaultError("An email and password are required.", "invalid_input")
    kdf = {"kdf": KDF_PBKDF2, "kdfIterations": PBKDF2_ITERATIONS}
    master_key = derive_master_key(password, email, kdf)
    user_key = os.urandom(64)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    private_der = private_key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    body = {
        "email": email,
        "name": name or email,
        "masterPasswordHash": master_password_hash(master_key, password),
        "masterPasswordHint": None,
        "key": encrypt(user_key, stretch(master_key)),
        "kdf": KDF_PBKDF2,
        "kdfIterations": PBKDF2_ITERATIONS,
        "keys": {
            "publicKey": _b64(public_der),
            "encryptedPrivateKey": encrypt(private_der, SymmetricKey.from_bytes(user_key)),
        },
    }
    owned = client is None
    http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)
    try:
        response = http.post("/identity/accounts/register", json=body)
        if response.status_code == 400 and "already" in response.text.lower():
            raise VaultError("A Vaultwarden account with this email already exists.", "exists")
        if response.status_code >= 400:
            raise VaultError(f"Vaultwarden refused the new account (HTTP {response.status_code}).", "rejected")
    except httpx.HTTPError as exc:
        raise VaultError("Vaultwarden is not answering.", "unreachable") from exc
    finally:
        if owned:
            http.close()


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
    key: SymmetricKey = field(repr=False)

    @property
    def mu3lab_id(self) -> str:
        return self.fields.get("mu3lab_id", "")


def _camel(value: Any) -> Any:
    """Accept both the legacy PascalCase and current camelCase JSON shapes."""
    if isinstance(value, list):
        return [_camel(item) for item in value]
    if isinstance(value, dict):
        return {(key[:1].lower() + key[1:]): _camel(item) for key, item in value.items()}
    return value


class VaultSession:
    """One authenticated, in-memory session against a Vaultwarden server."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._token = ""
        self._user_key: SymmetricKey | None = None

    def __enter__(self) -> VaultSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._token = ""
        self._user_key = None
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            return self._client.request(method, self.base_url + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise VaultError("Vaultwarden is not reachable from Mu3Lab.", "unreachable") from exc

    def _api(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request(method, path, json=body)
        if response.status_code >= 400:
            raise VaultError(f"Vaultwarden rejected a vault update (HTTP {response.status_code}).", "rejected")
        return _camel(response.json()) if response.content else {}

    def login(self, email: str, password: str, *, totp: str = "") -> None:
        email = email.strip().lower()
        if not email or not password:
            raise VaultError("Enter your Vaultwarden email and master password.", "invalid_input")
        response = self._request("POST", "/identity/accounts/prelogin", json={"email": email})
        if response.status_code != 200:
            raise VaultError("Vaultwarden did not answer its sign-in preflight.", "unreachable")
        master_key = derive_master_key(password, email, _camel(response.json()))
        form = {
            "grant_type": "password",
            "username": email,
            "password": master_password_hash(master_key, password),
            "scope": "api offline_access",
            "client_id": CLIENT_ID,
            "deviceType": str(DEVICE_TYPE_LINUX_CLI),
            "deviceIdentifier": str(uuid.uuid4()),
            "deviceName": "Mu3Lab vault setup",
        }
        if totp:
            form |= {"twoFactorToken": totp.strip(), "twoFactorProvider": "0", "twoFactorRemember": "0"}
        auth_email = base64.urlsafe_b64encode(email.encode("utf-8")).decode("ascii").rstrip("=")
        response = self._request("POST", "/identity/connect/token", data=form, headers={"Auth-Email": auth_email})
        payload = _camel(response.json()) if response.content else {}
        if response.status_code != 200:
            if payload.get("twoFactorProviders") or payload.get("twoFactorProviders2"):
                raise VaultError(
                    "Your vault uses two-step login. Enter the current code from your authenticator app.",
                    "two_factor_required",
                )
            raise VaultError("Vaultwarden did not accept that email and master password.", "invalid_credentials")
        encrypted_user_key = payload.get("key") or ""
        self._token = str(payload.get("access_token", ""))
        if not self._token or not encrypted_user_key:
            raise VaultError("Vaultwarden returned an incomplete sign-in response.", "rejected")
        self._user_key = SymmetricKey.from_bytes(decrypt_bytes(encrypted_user_key, stretch(master_key)))

    @property
    def user_key(self) -> SymmetricKey:
        if self._user_key is None:
            raise VaultError("The vault session is not signed in.", "invalid_state")
        return self._user_key

    def _item_key(self, cipher: dict[str, Any]) -> SymmetricKey:
        encrypted = cipher.get("key")
        return SymmetricKey.from_bytes(decrypt_bytes(encrypted, self.user_key)) if encrypted else self.user_key

    def sync(self) -> tuple[list[Folder], list[Login]]:
        data = self._api("GET", "/api/sync?excludeDomains=true")
        folders = [
            Folder(str(item["id"]), decrypt(item.get("name"), self.user_key)) for item in data.get("folders", [])
        ]
        logins: list[Login] = []
        for cipher in data.get("ciphers", []):
            if cipher.get("organizationId") or cipher.get("type") != LOGIN_ITEM or cipher.get("deletedDate"):
                continue
            try:
                key = self._item_key(cipher)
                login = cipher.get("login") or {}
                logins.append(
                    Login(
                        id=str(cipher["id"]),
                        name=decrypt(cipher.get("name"), key),
                        folder_id=cipher.get("folderId"),
                        username=decrypt(login.get("username"), key),
                        uris=[decrypt(uri.get("uri"), key) for uri in login.get("uris") or [] if uri.get("uri")],
                        fields={
                            decrypt(item.get("name"), key): decrypt(item.get("value"), key)
                            for item in cipher.get("fields") or []
                            if item.get("name")
                        },
                        raw=cipher,
                        key=key,
                    )
                )
            except (VaultError, ValueError):
                # An item this client cannot read is simply left alone.
                continue
        return folders, logins

    def create_folder(self, name: str) -> Folder:
        created = self._api("POST", "/api/folders", {"name": encrypt(name, self.user_key)})
        return Folder(str(created["id"]), name)

    def create_login(
        self,
        *,
        name: str,
        folder_id: str | None,
        username: str,
        password: str,
        uris: list[tuple[str, int | None]],
        notes: str,
        fields: dict[str, str],
    ) -> str:
        key = self.user_key
        body = {
            "type": LOGIN_ITEM,
            "folderId": folder_id,
            "organizationId": None,
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
        return str(self._api("POST", "/api/ciphers", body)["id"])

    def decrypt_password(self, login: Login) -> str:
        return decrypt((login.raw.get("login") or {}).get("password"), login.key)

    def replace_password(self, login: Login, password: str) -> None:
        """Rotate one managed password, keeping every other field untouched."""
        body = {key: value for key, value in login.raw.items() if key not in {"object", "edit", "viewPassword"}}
        current = dict(body.get("login") or {})
        history = list(body.get("passwordHistory") or [])
        if current.get("password"):
            history.insert(0, {"password": current["password"], "lastUsedDate": datetime.now(UTC).isoformat()})
        current["password"] = encrypt(password, login.key)
        body["login"] = current
        body["passwordHistory"] = history[:5]
        self._api("PUT", f"/api/ciphers/{login.id}", body)
