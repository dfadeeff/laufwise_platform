"""Envelope encryption for connection credentials (ADR-0003 D3).

A thevea connection holds the user's own login credentials. They are stored **Fernet-encrypted**
in `Connection.tokens_enc` and decrypted only transiently to build a client. The key comes from
`CONNECTION_ENC_KEY` (a urlsafe-base64 Fernet key); with no key configured, any credential
operation raises rather than silently storing plaintext — credentials are never written in the
clear by accident.

Callers encrypt a JSON string (e.g. `json.dumps({"username": ..., "password": ...})`); this
module is credential-shape-agnostic and only moves opaque strings in and out.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CredentialCryptoUnavailable(RuntimeError):
    """Raised when credentials cannot be en/decrypted (no key, or a key/ciphertext mismatch)."""


def _fernet() -> Fernet:
    key = settings.connection_enc_key
    if not key:
        raise CredentialCryptoUnavailable(
            "CONNECTION_ENC_KEY is not set — refusing to handle connection credentials"
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CredentialCryptoUnavailable(f"CONNECTION_ENC_KEY is not a valid Fernet key: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a credential string -> ciphertext token (safe to store in tokens_enc)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext token back to the credential string."""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialCryptoUnavailable(
            "cannot decrypt credential (wrong CONNECTION_ENC_KEY, or corrupted ciphertext)"
        ) from exc