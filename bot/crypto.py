"""Encryption of game tokens at rest (Fernet, keyed by ENCRYPTION_KEY)."""

from cryptography.fernet import Fernet, InvalidToken


class TokenDecryptError(Exception):
    """A stored token can't be decrypted, usually because ENCRYPTION_KEY changed."""


def validate_key(key: str) -> None:
    """Raise ValueError if `key` isn't a valid Fernet key."""
    Fernet(key.encode())


class TokenCipher:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, blob: bytes) -> str:
        try:
            return self._fernet.decrypt(blob).decode()
        except InvalidToken:
            raise TokenDecryptError("stored token can't be decrypted (was ENCRYPTION_KEY changed?)") from None
