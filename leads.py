# routers/auth.py
# ---------------------------------------------------------------------------
# Auth router — login + register
# Rails equivalent: Devise::SessionsController + Devise::RegistrationsController
#
# Rails → FastAPI:
#   POST /users/sign_in     → POST /api/v1/auth/login   (returns JWT)
#   POST /users             → POST /api/v1/auth/register
#   DELETE /users/sign_out  → POST /api/v1/auth/logout  (client deletes token)
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, hash_password, verify_password
from database import get_db
from models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """
    Rails: params.require(:user).permit(:email, :password)
    Sent as JSON body — NOT query params (passwords never go in URLs).
    """
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str | None = None
    last_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# Rails: Devise::SessionsController#create
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Rails: POST /users/sign_in
    Authenticates with email + password, returns JWT.
    Password is sent in JSON body — never in URL query params.
    """
    # Rails: User.find_by(email: params[:user][:email])
    result = await db.execute(
        select(User).where(
            User.email == body.email.lower(),
            User.active == True,
            User.deleted_at == None,
        )
    )
    user = result.scalar_one_or_none()

    # Rails: user&.valid_password?(params[:user][:password])
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        user_id=user.id,
        email=user.email,
        is_admin=user.admin,
    )
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# Rails: Devise::RegistrationsController#create
# ---------------------------------------------------------------------------
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Rails: POST /users (Devise registration)
    Creates a new user and returns JWT immediately.
    """
    # Check email uniqueness — Rails: validates :email, uniqueness: true
    existing = await db.execute(
        select(User).where(User.email == body.email.lower())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": {"email": ["has already been taken"]}},
        )

    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
    )
    db.add(user)
    await db.flush()  # Get user.id before commit

    token = create_access_token(
        user_id=user.id,
        email=user.email,
        is_admin=user.admin,
    )
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# Rails: Devise::SessionsController#destroy (DELETE /users/sign_out)
# JWT is stateless — client just discards the token.
# For server-side invalidation, add a token denylist (Redis recommended).
# ---------------------------------------------------------------------------
@router.post("/logout")
async def logout():
    """
    Rails: DELETE /users/sign_out
    JWT is stateless — instruct client to delete the token.
    """
    return {"message": "Logged out. Delete your token on the client side."}
