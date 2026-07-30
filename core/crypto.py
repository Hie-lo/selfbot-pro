"""
رمزنگاری AES-256 با Fernet
"""

from cryptography.fernet import Fernet, InvalidToken
from config import ENCRYPTION_KEY

_fernet = Fernet(
    ENCRYPTION_KEY.encode()
    if isinstance(ENCRYPTION_KEY, str)
    else ENCRYPTION_KEY
)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet.encrypt(
        plaintext.encode("utf-8")
    ).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(
            ciphertext.encode("utf-8")
        ).decode("utf-8")
    except InvalidToken:
        raise ValueError("Decryption failed: invalid key or corrupted data")


def encrypt_bytes(data: bytes) -> bytes:
    if not data:
        return b""
    return _fernet.encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    if not data:
        return b""
    try:
        return _fernet.decrypt(data)
    except InvalidToken:
        raise ValueError("Decryption failed: invalid key or corrupted data")