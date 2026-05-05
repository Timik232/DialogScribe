import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gigaam_transcriber.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    password = "secure_password_123"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_create_access_token():
    token = create_access_token(user_id="test-user-id", role="user")
    assert isinstance(token, str)
    payload = decode_token(token)
    assert payload["sub"] == "test-user-id"
    assert payload["role"] == "user"
    assert payload["type"] == "access"
    assert "exp" in payload


def test_create_refresh_token():
    token = create_refresh_token(user_id="test-user-id")
    assert isinstance(token, str)
    payload = decode_token(token)
    assert payload["sub"] == "test-user-id"
    assert payload["type"] == "refresh"
    assert "jti" in payload


def test_decode_expired_token():
    from unittest.mock import patch
    from datetime import datetime, timedelta
    from jose import jwt
    from gigaam_transcriber.auth import JWT_SECRET, JWT_ALGORITHM
    from fastapi import HTTPException

    expired_payload = {
        "sub": "test-user-id",
        "type": "access",
        "exp": datetime.utcnow() - timedelta(minutes=1),
        "iat": datetime.utcnow() - timedelta(minutes=16),
    }
    expired_token = jwt.encode(expired_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    with pytest.raises(HTTPException) as exc_info:
        decode_token(expired_token)
    assert exc_info.value.status_code == 401


def test_decode_invalid_token():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        decode_token("invalid.token.here")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user():
    from gigaam_transcriber.auth import get_current_user

    token = create_access_token(user_id="user-123", role="user")

    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_user.is_active = True
    mock_user.role = "user"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    credentials = MagicMock()
    credentials.credentials = token

    user = await get_current_user(credentials=credentials, db=mock_db)
    assert user.id == "user-123"


@pytest.mark.asyncio
async def test_get_current_user_inactive():
    from gigaam_transcriber.auth import get_current_user
    from fastapi import HTTPException

    token = create_access_token(user_id="user-123", role="user")

    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_user.is_active = False

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    credentials = MagicMock()
    credentials.credentials = token

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=credentials, db=mock_db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_admin_user():
    from gigaam_transcriber.auth import get_admin_user

    mock_user = MagicMock()
    mock_user.role = "admin"

    admin = await get_admin_user(current_user=mock_user)
    assert admin.role == "admin"


@pytest.mark.asyncio
async def test_get_admin_user_forbidden():
    from gigaam_transcriber.auth import get_admin_user
    from fastapi import HTTPException

    mock_user = MagicMock()
    mock_user.role = "user"

    with pytest.raises(HTTPException) as exc_info:
        await get_admin_user(current_user=mock_user)
    assert exc_info.value.status_code == 403
