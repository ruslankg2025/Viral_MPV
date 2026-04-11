from cryptography.fernet import Fernet, InvalidToken


class KeyCrypto:
    def __init__(self, master_key: str):
        if not master_key:
            raise RuntimeError(
                "PROCESSOR_KEY_ENCRYPTION_KEY is empty. "
                "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        try:
            self._f = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        except Exception as e:
            raise RuntimeError(f"PROCESSOR_KEY_ENCRYPTION_KEY is not a valid Fernet key: {e}") from e

    def encrypt(self, plaintext: str) -> bytes:
        return self._f.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._f.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as e:
            raise RuntimeError("failed to decrypt api key (wrong master key?)") from e


def mask_secret(secret: str, shown: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= shown * 2:
        return "*" * len(secret)
    prefix = secret[: min(6, len(secret) - shown)]
    return f"{prefix}***{secret[-shown:]}"
