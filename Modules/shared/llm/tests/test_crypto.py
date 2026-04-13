"""Unit-тесты KeyCrypto + mask_secret."""
from cryptography.fernet import Fernet

from viral_llm.keys.crypto import KeyCrypto, mask_secret


def test_crypto_roundtrip():
    key = Fernet.generate_key().decode()
    c = KeyCrypto(key)
    enc = c.encrypt("hello-world-12345")
    assert enc != b"hello-world-12345"
    assert c.decrypt(enc) == "hello-world-12345"


def test_mask_secret():
    assert mask_secret("sk-1234567890abcdef") == "sk-123***cdef"
    assert mask_secret("") == ""
    assert mask_secret("short") == "*****"
