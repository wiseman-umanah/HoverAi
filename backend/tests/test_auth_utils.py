"""Tests for authentication utilities (pure unit tests — no DB needed)."""

import time

import jwt

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings


class TestHashPassword:
    """Tests for the hash_password function."""

    def test_returns_string(self):
        """hash_password returns a non-empty string."""
        result = hash_password("mypassword")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hashes_are_unique(self):
        """Two calls with the same password produce different hashes (different salts)."""
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2

    def test_hash_is_bcrypt_format(self):
        """Hash starts with bcrypt prefix $2b$."""
        h = hash_password("test")
        assert h.startswith("$2b$")


class TestVerifyPassword:
    """Tests for the verify_password function."""

    def test_correct_password_returns_true(self):
        """verify_password returns True for a matching plain/hash pair."""
        plain = "correct_password"
        hashed = hash_password(plain)
        assert verify_password(plain, hashed) is True

    def test_wrong_password_returns_false(self):
        """verify_password returns False for a non-matching plain/hash pair."""
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_over_72_bytes_returns_false_instead_of_raising(self):
        """bcrypt 5 raises on >72 bytes; verify_password must just say no (B3)."""
        hashed = hash_password("a" * 72)
        assert verify_password("a" * 73, hashed) is False

    def test_empty_string_does_not_match(self):
        """An empty string does not match a real password hash."""
        hashed = hash_password("notempty")
        assert verify_password("", hashed) is False


class TestCreateAccessToken:
    """Tests for the create_access_token function."""

    def test_returns_string(self):
        """create_access_token returns a non-empty string."""
        token = create_access_token({"sub": "test-id"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_sub(self):
        """Decoded token payload contains the sub claim."""
        token = create_access_token({"sub": "user-123"})
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        assert payload["sub"] == "user-123"

    def test_token_contains_exp(self):
        """Decoded token payload contains a future expiry timestamp."""
        token = create_access_token({"sub": "user-123"})
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        assert payload["exp"] > time.time()

    def test_tampered_token_is_rejected(self):
        """A token signed with another secret fails PyJWT verification."""
        token = jwt.encode({"sub": "user-123"}, "x" * 40, algorithm="HS256")
        try:
            jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        except jwt.InvalidSignatureError:
            return
        raise AssertionError("tampered token was accepted")

    def test_caller_dict_is_not_mutated(self):
        """create_access_token does not modify the caller's dict."""
        data = {"sub": "user-123"}
        original_keys = set(data.keys())
        create_access_token(data)
        assert set(data.keys()) == original_keys
