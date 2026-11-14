import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


def derive_key(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


def engine_fernet() -> MultiFernet:
    """The engine's own key, ENGINE_SECRETS_KEY, first when it is set: new values are encrypted with it, so a
    new Django SECRET_KEY leaves them readable. The key derived from SECRET_KEY comes after, so values stored
    under it before still decrypt."""
    keys = []
    own = (getattr(settings, "ENGINE_SECRETS_KEY", "") or "").strip()
    if own:
        keys.append(Fernet(own.encode()))
    keys.append(Fernet(derive_key(settings.SECRET_KEY)))
    return MultiFernet(keys)


def encrypt_value(plaintext: str) -> str:
    return engine_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return engine_fernet().decrypt(ciphertext.encode()).decode()
