"""Tests for forgot-password and reset-password API endpoints."""

import hashlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.auth import auth_router
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def app(mock_db):
    _app = FastAPI()
    _app.include_router(auth_router)
    _app.dependency_overrides[get_db] = lambda: mock_db
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


def _make_user(
    user_id="test-id",
    email="test@test.com",
    username="testuser",
    role="user",
    is_active=True,
    password_hash="$2b$12$fakehash",
    reset_token_hash=None,
    reset_token_expires=None,
):
    user = MagicMock(spec=User)
    user.id = user_id
    user.email = email
    user.username = username
    user.password_hash = password_hash
    user.role = role
    user.is_active = is_active
    user.reset_token_hash = reset_token_hash
    user.reset_token_expires = reset_token_expires
    return user


GENERIC_MSG = "Если аккаунт с таким email существует, мы отправили ссылку для сброса пароля"


# ---------------------------------------------------------------------------
# Forgot-password tests
# ---------------------------------------------------------------------------

class TestForgotPassword:
    """POST /api/auth/forgot-password"""

    def test_existing_active_user_returns_generic_message(self, client, mock_db):
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )
        with patch("routers.auth.send_password_reset_email", new_callable=AsyncMock):
            resp = client.post("/api/auth/forgot-password", json={
                "email": "test@test.com",
            })
        assert resp.status_code == 200
        assert resp.json()["message"] == GENERIC_MSG

    def test_nonexistent_email_returns_same_message(self, client, mock_db):
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None)
        )
        resp = client.post("/api/auth/forgot-password", json={
            "email": "nobody@test.com",
        })
        assert resp.status_code == 200
        assert resp.json()["message"] == GENERIC_MSG

    def test_inactive_user_returns_same_message(self, client, mock_db):
        user = _make_user(is_active=False)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )
        resp = client.post("/api/auth/forgot-password", json={
            "email": "test@test.com",
        })
        assert resp.status_code == 200
        assert resp.json()["message"] == GENERIC_MSG

    def test_token_stored_as_sha256_hash(self, client, mock_db):
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        captured_token = None

        async def _capture_send(email, token, frontend_url=None):
            nonlocal captured_token
            captured_token = token

        with patch("routers.auth.send_password_reset_email", side_effect=_capture_send):
            resp = client.post("/api/auth/forgot-password", json={
                "email": "test@test.com",
            })

        assert resp.status_code == 200
        assert captured_token is not None

        expected_hash = hashlib.sha256(captured_token.encode()).hexdigest()
        assert user.reset_token_hash == expected_hash

    def test_token_expires_about_one_hour(self, client, mock_db):
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )
        before = datetime.utcnow()

        with patch("routers.auth.send_password_reset_email", new_callable=AsyncMock):
            resp = client.post("/api/auth/forgot-password", json={
                "email": "test@test.com",
            })

        after = datetime.utcnow()
        assert resp.status_code == 200
        assert user.reset_token_expires is not None

        delta = user.reset_token_expires - before
        assert timedelta(minutes=55) < delta < timedelta(hours=1, minutes=5)

    def test_email_is_actually_sent(self, client, mock_db):
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        with patch("routers.auth.send_password_reset_email", new_callable=AsyncMock) as mock_send:
            resp = client.post("/api/auth/forgot-password", json={
                "email": "test@test.com",
            })

        assert resp.status_code == 200
        mock_send.assert_awaited_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "test@test.com"
        assert len(call_args[0][1]) > 0  # token string

    def test_email_failure_does_not_leak_error(self, client, mock_db):
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        with patch(
            "routers.auth.send_password_reset_email",
            new_callable=AsyncMock,
            side_effect=Exception("SMTP down"),
        ):
            resp = client.post("/api/auth/forgot-password", json={
                "email": "test@test.com",
            })

        assert resp.status_code == 200
        assert resp.json()["message"] == GENERIC_MSG

    def test_empty_email_still_returns_generic_message(self, client, mock_db):
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None)
        )
        with patch("routers.auth.send_password_reset_email", new_callable=AsyncMock):
            resp = client.post("/api/auth/forgot-password", json={
                "email": "",
            })
        assert resp.status_code == 200
        assert resp.json()["message"] == GENERIC_MSG


# ---------------------------------------------------------------------------
# Reset-password tests
# ---------------------------------------------------------------------------

class TestResetPassword:
    """POST /api/auth/reset-password"""

    def _make_user_with_token(
        self,
        token="valid-reset-token",
        expired=False,
    ):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if expired:
            expires = datetime.utcnow() - timedelta(hours=1)
        else:
            expires = datetime.utcnow() + timedelta(hours=1)

        user = _make_user(
            is_active=True,
            password_hash="$2b$12$oldhashed",
            reset_token_hash=token_hash,
            reset_token_expires=expires,
        )
        return user, token

    def test_valid_token_resets_password(self, client, mock_db):
        user, token = self._make_user_with_token()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        with patch("routers.auth.hash_password", return_value="$2b$12$newhashed"):
            resp = client.post("/api/auth/reset-password", json={
                "token": token,
                "new_password": "newSecurePass123",
            })

        assert resp.status_code == 200
        assert resp.json()["message"] == "Пароль успешно изменён"
        assert user.password_hash == "$2b$12$newhashed"

    def test_token_cleared_after_use(self, client, mock_db):
        user, token = self._make_user_with_token()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        with patch("routers.auth.hash_password", return_value="$2b$12$newhashed"):
            resp = client.post("/api/auth/reset-password", json={
                "token": token,
                "new_password": "newSecurePass123",
            })

        assert resp.status_code == 200
        assert user.reset_token_hash is None
        assert user.reset_token_expires is None

    def test_second_attempt_with_same_token_fails(self, client, mock_db):
        user, token = self._make_user_with_token()
        call_count = 0

        def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(scalar_one_or_none=lambda: user)
            return MagicMock(scalar_one_or_none=lambda: None)

        mock_db.execute = AsyncMock(side_effect=_execute_side_effect)

        with patch("routers.auth.hash_password", return_value="$2b$12$newhashed"):
            # First attempt succeeds
            resp1 = client.post("/api/auth/reset-password", json={
                "token": token,
                "new_password": "newSecurePass123",
            })
            # Second attempt — token hash no longer in DB
            resp2 = client.post("/api/auth/reset-password", json={
                "token": token,
                "new_password": "anotherPass456",
            })

        assert resp1.status_code == 200
        assert resp2.status_code == 400

    def test_expired_token_returns_400(self, client, mock_db):
        user, token = self._make_user_with_token(expired=True)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: user)
        )

        resp = client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "newSecurePass123",
        })

        assert resp.status_code == 400
        assert "истекла" in resp.json()["detail"]
        # Token should be cleared on expiry too
        assert user.reset_token_hash is None
        assert user.reset_token_expires is None

    def test_invalid_token_returns_400(self, client, mock_db):
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None)
        )

        resp = client.post("/api/auth/reset-password", json={
            "token": "totally-invalid-token",
            "new_password": "newSecurePass123",
        })

        assert resp.status_code == 400
        assert "Недействительная" in resp.json()["detail"]

    def test_missing_token_returns_422(self, client, mock_db):
        resp = client.post("/api/auth/reset-password", json={
            "new_password": "newSecurePass123",
        })
        assert resp.status_code == 422

    def test_missing_password_returns_422(self, client, mock_db):
        resp = client.post("/api/auth/reset-password", json={
            "token": "some-token",
        })
        assert resp.status_code == 422

    def test_password_too_short_returns_422(self, client, mock_db):
        resp = client.post("/api/auth/reset-password", json={
            "token": "some-token",
            "new_password": "short",
        })
        assert resp.status_code == 422

    def test_empty_body_returns_422(self, client, mock_db):
        resp = client.post("/api/auth/reset-password", json={})
        assert resp.status_code == 422
