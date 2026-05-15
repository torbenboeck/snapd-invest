"""Symmetric encryption for secrets at rest.

`Cipher` is the abstraction the rest of the engine talks to. `FernetCipher`
is the default implementation. The single-tenant master key comes from the
`SNAPDINVEST_ENCRYPTION_KEY` env var (Fernet-format base64) and is injected
into one shared `FernetCipher` instance at engine startup.

Multi-tenant evolution path: introduce a `KeyProvider` indirection so per-
account or per-tenant keys can replace the single env-var key without
touching schema or call sites.
"""

from __future__ import annotations

from typing import Protocol

from cryptography.fernet import Fernet


class Cipher(Protocol):
    """Symmetric cipher with authenticated encryption.

    `encrypt(plaintext) -> ciphertext` and `decrypt(ciphertext) -> plaintext`.
    Both operate on `str` (the broker uses string tokens). Implementations
    must raise on malformed/tampered input.
    """

    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class FernetCipher:
    """Fernet (AES-128-CBC + HMAC-SHA256) with the supplied key.

    The key must be a 32-byte URL-safe base64 string (Fernet's required
    format). Use `generate_key()` to produce one.
    """

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def generate_key() -> str:
    """Generate a fresh Fernet key as a URL-safe base64 string."""
    return Fernet.generate_key().decode("ascii")
