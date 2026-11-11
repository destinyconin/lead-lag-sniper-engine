"""
AES-256-GCM encryption utility for exchange API keys, secrets, and passphrases.
Fixes Section 3.3 API security requirement: never store plaintext secrets in SQLite.
"""
import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretVault:
    def __init__(self, master_key_str: str | None = None):
        env_key = master_key_str or os.environ.get("SNIPER_MASTER_KEY")
        self.using_ephemeral_key = env_key is None
        raw_secret = env_key or "antigravity-lead-lag-sniper-dev-default-key-change-in-prod"
        # Derive a 256-bit (32-byte) key using SHA-256
        self._key = hashlib.sha256(raw_secret.encode("utf-8")).digest()
        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            raw = base64.b64decode(token.encode("ascii"))
            nonce, ciphertext = raw[:12], raw[12:]
            return self._aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception:
            return ""

    @staticmethod
    def mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "****"
        return f"{value[:4]}****{value[-4:]}"


vault = SecretVault()

