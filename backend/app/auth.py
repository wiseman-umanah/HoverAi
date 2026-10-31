"""Authentication utilities — pure functions with no database knowledge.

This module handles password hashing, JWT creation/validation, and refresh
token generation. All database interactions happen in the routers that call
these functions, except ``get_current_user`` which does one short lookup.

Design notes:
  - JWTs use PyJWT (python-jose is unmaintained). HS256, claims ``sub`` + ``exp``.
  - bcrypt 5 raises on inputs over 72 bytes instead of truncating (B3).
    Signup rejects such passwords at validation time (``schemas/auth.py``);
    ``verify_password`` simply returns False for them so signin answers 401.
  - ``get_current_user`` opens and closes its own session rather than using
    ``get_session``: a dependency-scoped session would stay checked out until
    after the response, pinning a pool connection through the whole /query
    pipeline (B6).
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.models import User

logger = logging.getLogger(__name__)

# OAuth2 scheme — extracts the Bearer token from the Authorization header.
# The tokenUrl makes Swagger's "Authorize" button POST to /signin automatically.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/signin")

# bcrypt only looks at the first 72 bytes; bcrypt>=5 raises on longer input.
BCRYPT_MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    """Hash a plain-text password using bcrypt.

    Callers must validate the length first (``SignupRequest`` does): bcrypt 5
    raises ``ValueError`` for passwords over 72 UTF-8 bytes.

    Args:
        plain: The raw password string provided by the user.

    Returns:
        A bcrypt hash string (UTF-8 decoded) safe to store in the database.

    Raises:
        ValueError: If the password exceeds 72 UTF-8 bytes.
    """
    if len(plain.encode()) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash.

    Args:
        plain: The raw password string to verify.
        hashed: The bcrypt hash string stored in the database.

    Returns:
        True if the password matches the hash, False otherwise (including
        passwords over 72 bytes, which can never have been hashed).
    """
    if len(plain.encode()) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict) -> str:
    """Create a signed JWT access token.

    The token expires after ``settings.jwt_expire_minutes`` minutes (default 15).

    Args:
        data: Payload dict to encode into the token. A copy is made
              internally so the caller's dict is not mutated.

    Returns:
        A signed JWT string.
    """
    payload = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload["exp"] = expire
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token() -> tuple[str, datetime]:
    """Generate a cryptographically random opaque refresh token.

    The token is a 64-byte (128 hex-char) random value — not a JWT. It is
    stored in the ``refresh_tokens`` table and compared on each refresh call.

    Returns:
        A tuple of ``(token_string, expires_at)`` where ``expires_at`` is a
        timezone-aware UTC datetime ``settings.jwt_refresh_expire_days`` days
        from now.
    """
    token = secrets.token_hex(64)  # 128 hex chars
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days)
    return token, expires_at


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """FastAPI dependency that validates a JWT and returns the current User.

    Extracts the Bearer token from the ``Authorization`` header, validates
    the signature and expiry, then fetches the matching ``User`` row in a
    short-lived session that is closed before the route body runs. The
    returned instance is detached; routes that modify it must ``add`` it to
    their own session.

    Args:
        token: JWT string injected by the ``OAuth2PasswordBearer`` scheme.

    Returns:
        The authenticated ``User`` SQLModel instance.

    Raises:
        HTTPException 401: If the token is missing, invalid, expired, or the
            user no longer exists in the database.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = uuid.UUID(user_id_str)
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_exception from None

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    return user
