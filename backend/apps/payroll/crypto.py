"""Encryption for bank details. Only the encrypted text is stored in the database."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = settings.PAYROLL_ENCRYPTION_KEY
    if not key:
        if not settings.DEBUG:
            raise ImproperlyConfigured("PAYROLL_ENCRYPTION_KEY must be set before bank details can be stored.")
        # Development and tests only: a key derived from the (insecure) secret key.
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ImproperlyConfigured("A stored bank detail cannot be decrypted: the encryption key has changed.")
