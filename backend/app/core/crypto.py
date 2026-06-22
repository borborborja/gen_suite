"""AES-256-GCM envelope encryption for secrets at rest (provider API keys, FS cookies).

Used from Phase 3 onward. Lazy-initialised so the app boots in Phase 0 without a master
key configured. Ciphertext + nonce are stored separately; ``key_version`` (in the calling
table) supports rotation without downtime.
"""
from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..settings import settings

KEY_VERSION = 1


class SecretBox:
    def __init__(self, master_key_b64: str):
        if not master_key_b64:
            raise RuntimeError("SUITE_MASTER_KEY is not configured")
        key = base64.b64decode(master_key_b64)
        if len(key) != 32:
            raise RuntimeError("SUITE_MASTER_KEY must decode to exactly 32 bytes")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str) -> tuple[bytes, bytes]:
        """Returns (ciphertext, nonce)."""
        nonce = os.urandom(12)
        return self._aes.encrypt(nonce, plaintext.encode(), None), nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes) -> str:
        return self._aes.decrypt(nonce, ciphertext, None).decode()


@lru_cache
def get_secret_box() -> SecretBox:
    return SecretBox(settings.suite_master_key)
