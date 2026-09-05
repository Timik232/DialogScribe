import hashlib
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import (
    REFRESH_TOKEN_EXPIRE_DAYS,
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
from gigaam_transcriber.sessions import (
    create_refresh_session,
    get_refresh_session,
    hash_jti,
    revoke_all_user_sessions,
    revoke_refresh_chain,
    rotate_refresh_session,
)
from gigaam_transcriber.settings import is_development

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _set_auth_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    # Secure is mandatory outside development; localhost http dev keeps it off.
    secure = not is_development()
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_COOKIE_MAX_AGE,
        path="/api/auth",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_COOKIE_MAX_AGE,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key="refresh_token", path="/api/auth")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")


def _validate_csrf(header_token: str | None, cookie_token: str | None) -> None:
    if (
        not header_token
        or not cookie_token
        or not secrets.compare_digest(header_token, cookie_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


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
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
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

    jti = str(uuid.uuid4())
    refresh_token = create_refresh_token(user.id, jti)
    await create_refresh_session(
        db,
        user_id=user.id,
        jti=jti,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )

    _set_auth_cookies(response, refresh_token, secrets.token_urlsafe(32))

    return TokenResponse(access_token=access_token)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str = Cookie(None),
    csrf_token: str = Cookie(None, alias=CSRF_COOKIE_NAME),
    x_csrf_token: str = Header(None, alias=CSRF_HEADER_NAME),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    _validate_csrf(x_csrf_token, csrf_token)

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session = await get_refresh_session(db, hash_jti(jti))
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if session.revoked_at is not None:
        # Reuse of an already-consumed token: assume theft and kill the
        # whole descendant chain before rejecting. get_db() rolls back on
        # handler exceptions, so the chain-kill must be committed first.
        await revoke_refresh_chain(db, session)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if session.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_token = create_access_token(user.id, user.role)

    new_jti = str(uuid.uuid4())
    new_refresh = create_refresh_token(user.id, new_jti)
    await rotate_refresh_session(
        db,
        session,
        new_jti,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )
    _set_auth_cookies(response, new_refresh, csrf_token or secrets.token_urlsafe(32))

    return TokenResponse(access_token=access_token)


@auth_router.post("/logout")
async def logout(
    response: Response,
    refresh_token: str = Cookie(None),
    csrf_token: str = Cookie(None, alias=CSRF_COOKIE_NAME),
    x_csrf_token: str = Header(None, alias=CSRF_HEADER_NAME),
    db: AsyncSession = Depends(get_db),
):
    if refresh_token:
        _validate_csrf(x_csrf_token, csrf_token)
        try:
            payload = decode_token(refresh_token)
        except HTTPException:
            payload = {}
        jti = payload.get("jti") if payload.get("type") == "refresh" else None
        if jti:
            session = await get_refresh_session(db, hash_jti(jti))
            if session:
                await revoke_refresh_chain(db, session)
    _clear_auth_cookies(response)
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
        raise HTTPException(status_code=400, detail="Недействительная или истёкшая ссылка")

    user.password_hash = hash_password(body.new_password)

    user.reset_token_hash = None
    user.reset_token_expires = None
    await revoke_all_user_sessions(db, user.id)
    await db.flush()
    await db.commit()

    return {"message": "Пароль успешно изменён"}
