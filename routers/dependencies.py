# routers/dependencies.py
# ---------------------------------------------------------------------------
# Shared FastAPI dependencies.
# Rails equivalent: ApplicationController before_action filters
#   - authenticate_user! (Devise)
#   - current_user helper
# ---------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decode_access_token
from database import get_db
from models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class UserContext:
    """
    Rails: current_user — the authenticated User ActiveRecord object.
    Lightweight dataclass populated from JWT + DB lookup.
    """
    user_id: int
    email: str
    is_admin: bool = False


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> UserContext:
    """
    Rails: before_action :authenticate_user! (Devise)
    Validates JWT Bearer token AND verifies user still exists + is active.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload.get("sub", 0))

    # Rails: Devise checks user is still active on each request
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.active == True,
            User.deleted_at == None,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UserContext(user_id=user.id, email=user.email, is_admin=user.admin)


async def get_admin_user(
    current_user: UserContext = Depends(get_current_user),
) -> UserContext:
    """Rails: before_action :require_admin"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
