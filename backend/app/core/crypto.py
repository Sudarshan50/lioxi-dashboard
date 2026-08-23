from cryptography.fernet import Fernet

from app.config import get_settings


class SecretBox:
    """Reversible encryption for credentials the backend must decrypt to call Azure.

    This is intentionally not a one-way hash: the service principal secret has to be
    recovered at sync time to authenticate against Azure Resource Manager.
    """

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


def get_secret_box() -> SecretBox:
    return SecretBox(get_settings().encryption_key)
