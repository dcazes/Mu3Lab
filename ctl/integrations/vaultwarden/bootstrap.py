"""Minimal account-registration crypto; all routine vault operations use bw."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
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
