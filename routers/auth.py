import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from gigaam_transcriber.database import get_db
from gigaam_transcriber.email import send_password_reset_email
from gigaam_transcriber.models import User

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class RegisterRequest(BaseModel):
    email: str
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    role: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


@auth_router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if not EMAIL_REGEX.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email format")

    existing = await db.execute(
        select(User).where((User.email == body.email) | (User.username == body.username))
    )
    if existing.scalar_one_or_none():
        email_check = await db.execute(select(User).where(User.email == body.email))
        if email_check.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        role="user",
        is_active=False,
    )
    db.add(user)
    await db.flush()

    return {"user_id": user.id, "username": user.username, "email": user.email}


@auth_router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where((User.email == body.login) | (User.username == body.login))
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        if user.approved_at is None:
            raise HTTPException(
                status_code=403,
                detail={"reason": "pending_approval", "message": "Account pending admin approval"},
            )
        raise HTTPException(
            status_code=403,
            detail={"reason": "account_disabled", "message": "Account disabled by administrator"},
        )

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/api/auth",
    )

    return TokenResponse(access_token=access_token)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    refresh_token: str = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_token = create_access_token(user.id, user.role)

    new_refresh = create_refresh_token(user.id)
    response.set_cookie(
        key="refresh_token",
        value=new_refresh,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/api/auth",
    )

    return TokenResponse(access_token=access_token)


@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="refresh_token", path="/api/auth")
    return {"message": "Logged out"}


@auth_router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return UserResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
    )


@auth_router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    GENERIC_MSG = "Если аккаунт с таким email существует, мы отправили ссылку для сброса пароля"

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user and user.is_active:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.utcnow() + timedelta(hours=1)

        user.reset_token_hash = token_hash
        user.reset_token_expires = expires
        await db.flush()

        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        try:
            await send_password_reset_email(user.email, token, frontend_url)
        except Exception:
            pass

    return {"message": GENERIC_MSG}


@auth_router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()

    result = await db.execute(
        select(User).where(User.reset_token_hash == token_hash)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Недействительная или истёкшая ссылка")

    if user.reset_token_expires and user.reset_token_expires < datetime.utcnow():
        user.reset_token_hash = None
        user.reset_token_expires = None
        await db.flush()
        raise HTTPException(status_code=400, detail="Ссылка для сброса истекла")

    user.password_hash = hash_password(body.new_password)

    user.reset_token_hash = None
    user.reset_token_expires = None
    await db.flush()
    await db.commit()

    return {"message": "Пароль успешно изменён"}
