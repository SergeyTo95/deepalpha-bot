from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class ConnectorCryptoError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = 503 if code == "velia_connector_encryption_not_configured" else 400


def _fernet() -> Fernet:
    raw = str(os.getenv("VELIA_CONNECTOR_FERNET_KEY") or "").strip()
    if not raw:
        raise ConnectorCryptoError("velia_connector_encryption_not_configured")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ConnectorCryptoError("velia_connector_encryption_key_invalid") from exc


def configured() -> bool:
    try:
        _fernet()
        return True
    except ConnectorCryptoError:
        return False


def encrypt_secret(value: str) -> str:
    secret = str(value or "")
    if not secret:
        raise ConnectorCryptoError("velia_connector_secret_empty")
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    ciphertext = str(value or "").strip()
    if not ciphertext:
        raise ConnectorCryptoError("velia_connector_secret_missing")
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ConnectorCryptoError("velia_connector_secret_invalid") from exc
