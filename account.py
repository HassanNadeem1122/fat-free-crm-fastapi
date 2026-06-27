# auth.py
# ---------------------------------------------------------------------------
# Authentication utilities.
# Rails equivalent: Devise password encryption + session management
#
# Rails → FastAPI:
#   Devise BCrypt password hashing → passlib CryptContext (bcrypt)
#   Devise session cookie          → JWT Bearer token
#   current_user helper            → Depends(get_current_user) in routers
# ---------------------------------------------------------------------------

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
import jwt as pyjwt

# ---------------------------------------------------------------------------
# Password hashing
# Rails: Devise uses BCrypt with cost factor 12 by default
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Rails: Devise.stretches = 12 (BCrypt cost)"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Rails: user.valid_password?(password)"""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT config
# Rails: Devise.secret_key → ENV["SECRET_KEY_BASE"]
# ---------------------------------------------------------------------------
JWT_SECRET: str = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def create_access_token(user_id: int, email: str, is_admin: bool = False) -> str:
    """
    Rails: Devise generates a session token stored in cookie.
    FastAPI: We issue a stateless JWT Bearer token instead.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "admin": is_admin,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate JWT. Returns payload or None."""
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.PyJWTError:
        return None
