"""Tests for `snapd_invest.crypto`."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from snapd_invest.crypto import FernetCipher, generate_key


class TestFernetCipher:
    def test_roundtrip_returns_original_plaintext(self) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        plaintext = "saxo-refresh-token-abc123"
        assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext

    def test_encrypt_produces_different_ciphertexts_for_same_plaintext(self) -> None:
        # Fernet includes a random IV; the same plaintext encrypts differently each call
        cipher = FernetCipher(Fernet.generate_key())
        assert cipher.encrypt("x") != cipher.encrypt("x")

    def test_decrypt_with_wrong_key_raises(self) -> None:
        a = FernetCipher(Fernet.generate_key())
        b = FernetCipher(Fernet.generate_key())
        ciphertext = a.encrypt("secret")
        with pytest.raises(InvalidToken):
            b.decrypt(ciphertext)

    def test_decrypt_tampered_ciphertext_raises(self) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        ciphertext = cipher.encrypt("secret")
        tampered = ciphertext[:-2] + "ZZ"
        with pytest.raises(InvalidToken):
            cipher.decrypt(tampered)

    def test_init_with_invalid_key_raises_at_construction(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            FernetCipher(b"not-a-valid-fernet-key")


class TestGenerateKey:
    def test_generates_a_fernet_compatible_key(self) -> None:
        key = generate_key()
        # Must be usable as a Fernet key
        FernetCipher(key.encode())

    def test_each_call_returns_a_different_key(self) -> None:
        assert generate_key() != generate_key()
