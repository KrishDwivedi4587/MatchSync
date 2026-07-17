"""Symmetric encryption for OAuth tokens at rest (Stage 1 crown-jewels rule).

Stage 3 left ``OAuthToken`` columns to be populated with *already-encrypted*
strings; this is where that encryption lives. Uses Fernet (AES-128-CBC + HMAC)
with a key derived from ``SECRET_KEY``.

NOTE: deriving the Fernet key from ``SECRET_KEY`` is acceptable for the current
single-key setup. The ``token_version`` column on ``OAuthToken`` exists so a
future KMS-backed, rotate-able key scheme can re-encrypt without a schema
change.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.exceptions.base import AppError


class TokenEncryptor:
    def __init__(self, settings: Settings) -> None:
        # Fernet needs a 32-byte urlsafe-base64 key; derive it deterministically.
        digest = hashlib.sha256(settings.secret_key.get_secret_value().encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:  # wrong key / corrupted data
            raise AppError("Failed to decrypt stored token.") from exc
