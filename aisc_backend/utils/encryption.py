import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def derive_key(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


def encrypt_value(plaintext: str) -> str:
    return Fernet(derive_key(settings.SECRET_KEY)).encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return Fernet(derive_key(settings.SECRET_KEY)).decrypt(ciphertext.encode()).decode()
