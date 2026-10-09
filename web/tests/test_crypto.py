"""T006：Fernet 加解密唯一口径与密钥掩码。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from web.app.crypto import decrypt_secret, encrypt_secret, mask_secret


@pytest.fixture()
def key_bytes() -> bytes:
    return Fernet.generate_key()


def test_roundtrip(key_bytes):
    plaintext = "sk-mineru-abcdef1234567890"
    token = encrypt_secret(plaintext, key_bytes)
    assert token != plaintext
    assert decrypt_secret(token, key_bytes) == plaintext


def test_ciphertext_has_no_plaintext_substring(key_bytes):
    plaintext = "sk-deepseek-zzzz9999-aaaa"
    token = encrypt_secret(plaintext, key_bytes)
    assert plaintext not in token
    assert "deepseek" not in token


def test_random_iv_makes_tokens_differ(key_bytes):
    assert encrypt_secret("same-secret", key_bytes) != encrypt_secret(
        "same-secret", key_bytes
    )


def test_tampered_token_raises(key_bytes):
    token = encrypt_secret("secret-value", key_bytes)
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(InvalidToken):
        decrypt_secret(tampered, key_bytes)


def test_wrong_key_fails():
    token = encrypt_secret("secret-value", Fernet.generate_key())
    with pytest.raises(InvalidToken):
        decrypt_secret(token, Fernet.generate_key())


def test_mask_long_secret():
    mask = mask_secret("mineru-secret-1234567890ab")
    assert mask.startswith("••••")
    assert mask.endswith("90ab")
    assert "mineru" not in mask
    assert "secret" not in mask
    assert mask != "mineru-secret-1234567890ab"


def test_mask_short_secret():
    assert mask_secret("ab") == "••••"
    assert mask_secret("abc") == "••••"
    assert mask_secret("abcd") == "••••abcd"
