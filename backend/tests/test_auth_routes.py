"""Tests for POST /api/v1/auth/ routes — signup, signin, refresh, signout."""

import httpx
import pytest


class TestSignup:
    """Integration tests for POST /api/v1/auth/signup."""

    async def test_signup_returns_201_and_tokens(self, client: httpx.AsyncClient):
        """Successful signup returns HTTP 201, access_token, and refresh_token."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "test@example.com",
                "full_name": "Test User",
                "password": "strongpassword1",
                "language": "en-pidgin",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20
        assert len(body["refresh_token"]) == 128  # 64-byte hex = 128 chars

    async def test_duplicate_email_returns_409(self, client: httpx.AsyncClient):
        """Registering the same email twice returns HTTP 409 Conflict."""
        payload = {
            "email": "duplicate@example.com",
            "full_name": "Dup User",
            "password": "strongpassword1",
            "language": "en",
        }
        first = await client.post("/api/v1/auth/signup", json=payload)
        assert first.status_code == 201

        second = await client.post("/api/v1/auth/signup", json=payload)
        assert second.status_code == 409
        assert "already exists" in second.json()["detail"]

    async def test_short_password_returns_422(self, client: httpx.AsyncClient):
        """Password shorter than 8 chars fails Pydantic validation (HTTP 422)."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "short@example.com",
                "full_name": "Short Pw",
                "password": "abc",
                "language": "en",
            },
        )
        assert resp.status_code == 422

    async def test_invalid_email_returns_422(self, client: httpx.AsyncClient):
        """An invalid email address fails Pydantic validation (HTTP 422)."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "not-an-email",
                "full_name": "Bad Email",
                "password": "strongpassword1",
                "language": "en",
            },
        )
        assert resp.status_code == 422


class TestSignin:
    """Integration tests for POST /api/v1/auth/signin."""

    async def test_signin_with_correct_credentials_returns_tokens(self, client: httpx.AsyncClient):
        """Signing in with valid credentials returns HTTP 200, access + refresh token."""
        await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "signin@example.com",
                "full_name": "Sign In User",
                "password": "mypassword123",
                "language": "yo",
            },
        )
        resp = await client.post(
            "/api/v1/auth/signin",
            json={"email": "signin@example.com", "password": "mypassword123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert len(body["refresh_token"]) == 128

    async def test_signin_wrong_password_returns_401(self, client: httpx.AsyncClient):
        """Wrong password returns HTTP 401 Unauthorized."""
        await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "wrongpw@example.com",
                "full_name": "Wrong Pw",
                "password": "correctpassword",
                "language": "en",
            },
        )
        resp = await client.post(
            "/api/v1/auth/signin",
            json={"email": "wrongpw@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    async def test_signin_unknown_email_returns_401(self, client: httpx.AsyncClient):
        """Email that was never registered returns HTTP 401 Unauthorized."""
        resp = await client.post(
            "/api/v1/auth/signin",
            json={"email": "ghost@example.com", "password": "anything"},
        )
        assert resp.status_code == 401

    async def test_signin_with_oauth2_form_fields(self, client: httpx.AsyncClient):
        """Signing in via OAuth2 form fields (Swagger Authorize dialog) works."""
        await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "formlogin@example.com",
                "full_name": "Form Login",
                "password": "formpassword1",
                "language": "en",
            },
        )
        resp = await client.post(
            "/api/v1/auth/signin",
            data={"username": "formlogin@example.com", "password": "formpassword1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body


class TestRefresh:
    """Integration tests for POST /api/v1/auth/refresh."""

    async def test_refresh_returns_new_token_pair(self, client: httpx.AsyncClient):
        """A valid refresh token exchanges for a new access + refresh token pair."""
        signup_resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "refresh@example.com",
                "full_name": "Refresh User",
                "password": "refreshpass1",
                "language": "en",
            },
        )
        original_refresh = signup_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        # Refresh token must rotate (new opaque value each time)
        assert body["refresh_token"] != original_refresh

    async def test_refresh_old_token_is_revoked_after_rotation(self, client: httpx.AsyncClient):
        """After rotation, the old refresh token is rejected with 401."""
        signup_resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "rotate@example.com",
                "full_name": "Rotate User",
                "password": "rotatepass1",
                "language": "en",
            },
        )
        original_refresh = signup_resp.json()["refresh_token"]

        # First use — succeeds and rotates
        await client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})

        # Second use of the same (now-revoked) token — must fail
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert resp.status_code == 401

    async def test_refresh_with_invalid_token_returns_401(self, client: httpx.AsyncClient):
        """A completely made-up refresh token returns HTTP 401."""
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "a" * 128},
        )
        assert resp.status_code == 401


class TestSignout:
    """Integration tests for POST /api/v1/auth/signout."""

    async def test_signout_revokes_refresh_token(self, client: httpx.AsyncClient):
        """After signout, the refresh token can no longer be used."""
        signup_resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "signout@example.com",
                "full_name": "Signout User",
                "password": "signoutpass1",
                "language": "en",
            },
        )
        refresh_token = signup_resp.json()["refresh_token"]

        # Sign out
        signout_resp = await client.post(
            "/api/v1/auth/signout",
            json={"refresh_token": refresh_token},
        )
        assert signout_resp.status_code == 204

        # Attempt to use the revoked refresh token — must fail
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 401

    async def test_signout_is_idempotent(self, client: httpx.AsyncClient):
        """Signing out twice with the same token returns 204 both times."""
        signup_resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "idem@example.com",
                "full_name": "Idem User",
                "password": "idempassword1",
                "language": "en",
            },
        )
        refresh_token = signup_resp.json()["refresh_token"]

        first = await client.post("/api/v1/auth/signout", json={"refresh_token": refresh_token})
        second = await client.post("/api/v1/auth/signout", json={"refresh_token": refresh_token})
        assert first.status_code == 204
        assert second.status_code == 204


class TestPasswordLength:
    """B3: bcrypt 5 raises on passwords over 72 bytes — must never be a 500."""

    async def test_signup_password_over_72_bytes_returns_422(self, client: httpx.AsyncClient):
        """A 73-byte ASCII password is rejected by validation."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "long@example.com",
                "full_name": "Long Pw",
                "password": "a" * 73,
                "language": "en",
            },
        )
        assert resp.status_code == 422
        assert "72 bytes" in resp.text

    async def test_signup_multibyte_password_over_72_bytes_returns_422(
        self, client: httpx.AsyncClient
    ):
        """30 characters can still exceed 72 bytes when they are multibyte."""
        password = "é" * 37  # 37 chars, 74 UTF-8 bytes
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "accents@example.com",
                "full_name": "Accents",
                "password": password,
                "language": "en",
            },
        )
        assert resp.status_code == 422

    async def test_signup_password_exactly_72_bytes_is_accepted(
        self, client: httpx.AsyncClient
    ):
        """The boundary value itself is fine."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "boundary@example.com",
                "full_name": "Boundary",
                "password": "b" * 72,
                "language": "en",
            },
        )
        assert resp.status_code == 201

    async def test_signin_password_over_72_bytes_returns_401(
        self, client: httpx.AsyncClient, signup
    ):
        """Signin with an over-long password is a normal auth failure, not a 500."""
        await signup(client, "existing@example.com")
        resp = await client.post(
            "/api/v1/auth/signin",
            json={"email": "existing@example.com", "password": "x" * 200},
        )
        assert resp.status_code == 401


class TestRefreshRotation:
    """B5: atomic refresh-token rotation."""

    async def test_second_use_of_same_refresh_token_returns_401(
        self, client: httpx.AsyncClient, signup
    ):
        """Only the first exchange of a refresh token succeeds; the new one works."""
        tokens = await signup(client, "rotation@example.com")
        first = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert second.status_code == 401
        assert second.json()["detail"]

        # The rotated token is valid exactly once too.
        rotated = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
        )
        assert rotated.status_code == 200

    async def test_expired_refresh_token_returns_401(self, client: httpx.AsyncClient, signup):
        """A token past expires_at cannot be exchanged, even if never revoked."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from app.db import AsyncSessionLocal
        from app.models.models import RefreshToken

        tokens = await signup(client, "expired@example.com")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.token == tokens["refresh_token"])  # type: ignore[arg-type]
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await session.commit()

        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert resp.status_code == 401

    async def test_new_access_token_works(self, client: httpx.AsyncClient, signup):
        """The access token issued by /refresh authenticates /users/me."""
        tokens = await signup(client, "access@example.com")
        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        me = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["email"] == "access@example.com"


class TestUserUpdateValidation:
    """B13: PATCH /users/me and /users/me/settings reject out-of-range values."""

    async def test_unknown_language_is_rejected(self, client, auth_headers):
        """Only codes from LANGUAGE_MAP are accepted."""
        resp = await client.patch(
            "/api/v1/users/me", json={"language": "klingon"}, headers=auth_headers
        )
        assert resp.status_code == 422
        resp = await client.patch("/api/v1/users/me", json={"language": "yo"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["language"] == "yo"

    async def test_signup_unknown_language_is_rejected(self, client):
        """Signup validates the language code too."""
        resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "lang@example.com",
                "full_name": "Lang",
                "password": "strongpassword1",
                "language": "klingon",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [{"overlay_opacity": 0.1}, {"overlay_opacity": 1.5}, {"mic_sensitivity": 0}, {"mic_sensitivity": 11}],
    )
    async def test_settings_out_of_range_rejected(self, client, auth_headers, body):
        """Opacity is 0.4–1.0 and mic sensitivity 1–10."""
        resp = await client.patch("/api/v1/users/me/settings", json=body, headers=auth_headers)
        assert resp.status_code == 422
