"""Authentication schemas — request bodies and response models for auth routes."""

from pydantic import BaseModel, EmailStr, Field, field_validator

# bcrypt hashes only the first 72 bytes and bcrypt>=5 raises beyond that (B3).
# Multibyte characters mean 72 *characters* can exceed this, so check bytes.
_MAX_PASSWORD_BYTES = 72


class SignupRequest(BaseModel):
    """Request body for POST /auth/signup.

    Attributes:
        email: User's email address (validated as RFC-compliant email).
        full_name: User's display name.
        password: Plain-text password — hashed server-side, never stored raw.
        language: Preferred language code (default Nigerian Pidgin).
        device_id: Optional client identifier used to scope refresh tokens
            (e.g. ``"electron-linux"``). Different devices get independent
            refresh token lifetimes.
    """

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    language: str = "en-pidgin"
    device_id: str | None = None

    @field_validator("password")
    @classmethod
    def _password_fits_bcrypt(cls, value: str) -> str:
        """Reject passwords longer than bcrypt's 72-byte input limit.

        Args:
            value: The submitted password.

        Returns:
            The password unchanged.

        Raises:
            ValueError: If the UTF-8 encoding exceeds 72 bytes (→ HTTP 422).
        """
        if len(value.encode()) > _MAX_PASSWORD_BYTES:
            raise ValueError(
                "Password is too long — use at most 72 bytes "
                "(fewer characters if it contains accents or emoji)."
            )
        return value


class SigninRequest(BaseModel):
    """Request body for POST /auth/signin.

    Attributes:
        email: Registered email address.
        password: Plain-text password for verification.
        device_id: Optional client identifier used to scope refresh tokens.
    """

    email: EmailStr
    password: str
    device_id: str | None = None


class RefreshRequest(BaseModel):
    """Request body for POST /auth/refresh.

    Attributes:
        refresh_token: The opaque refresh token issued at sign-in / sign-up.
    """

    refresh_token: str


class SignoutRequest(BaseModel):
    """Request body for POST /auth/signout.

    Attributes:
        refresh_token: The refresh token to revoke. The access token is
            stateless so only the refresh token needs server-side revocation.
    """

    refresh_token: str


class TokenResponse(BaseModel):
    """Response body returned after successful sign-up, sign-in, or token refresh.

    Attributes:
        access_token: Short-lived signed JWT (15 min). Attach as
            ``Authorization: Bearer <token>`` on every API request.
        refresh_token: Long-lived opaque token (90 days). Store securely
            (e.g. the OS keychain). Use ``POST /auth/refresh`` to
            obtain a new access token when the current one expires.
        token_type: Always ``"bearer"`` per OAuth2 convention.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
