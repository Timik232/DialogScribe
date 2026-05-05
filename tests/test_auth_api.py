from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.auth import auth_router
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User


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


def _make_user(user_id="test-id", email="test@test.com", username="testuser", role="user", is_active=True, approved_at=None):
    user = MagicMock(spec=User)
    user.id = user_id
    user.email = email
    user.username = username
    user.password_hash = "$2b$12$fakehash"
    user.role = role
    user.is_active = is_active
    user.approved_at = approved_at
    return user


class TestRegister:
    def test_register_success(self, client, mock_db):
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        with patch("routers.auth.hash_password", return_value="hashed"):
            resp = client.post("/api/auth/register", json={
                "email": "new@test.com",
                "username": "newuser",
                "password": "password123",
            })
        assert resp.status_code == 201
        data = resp.json()
        assert "user_id" in data
        assert data["username"] == "newuser"

    def test_register_duplicate_email(self, client, mock_db):
        existing_user = _make_user()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing_user))
        resp = client.post("/api/auth/register", json={
            "email": "test@test.com",
            "username": "newuser",
            "password": "password123",
        })
        assert resp.status_code == 409

    def test_register_weak_password(self, client):
        resp = client.post("/api/auth/register", json={
            "email": "new@test.com",
            "username": "newuser",
            "password": "short",
        })
        assert resp.status_code == 422

    def test_register_invalid_email(self, client, mock_db):
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        resp = client.post("/api/auth/register", json={
            "email": "not-an-email",
            "username": "newuser",
            "password": "password123",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_login_success(self, client, mock_db):
        user = _make_user()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
        with patch("routers.auth.verify_password", return_value=True):
            resp = client.post("/api/auth/login", json={
                "login": "test@test.com",
                "password": "password123",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "refresh_token" in resp.cookies

    def test_login_wrong_password(self, client, mock_db):
        user = _make_user()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
        with patch("routers.auth.verify_password", return_value=False):
            resp = client.post("/api/auth/login", json={
                "login": "test@test.com",
                "password": "wrongpassword",
            })
        assert resp.status_code == 401

    def test_login_inactive_user(self, client, mock_db):
        user = _make_user(is_active=False, approved_at=None)
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
        with patch("routers.auth.verify_password", return_value=True):
            resp = client.post("/api/auth/login", json={
                "login": "test@test.com",
                "password": "password123",
            })
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["reason"] == "pending_approval"

    def test_login_disabled_user(self, client, mock_db):
        from datetime import datetime
        user = _make_user(is_active=False, approved_at=datetime(2025, 1, 1))
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
        with patch("routers.auth.verify_password", return_value=True):
            resp = client.post("/api/auth/login", json={
                "login": "test@test.com",
                "password": "password123",
            })
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["reason"] == "account_disabled"


class TestRefresh:
    def test_refresh_success(self, client, mock_db):
        user = _make_user()
        from gigaam_transcriber.auth import create_refresh_token
        rt = create_refresh_token("test-id")
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))
        resp = client.post("/api/auth/refresh", cookies={"refresh_token": rt})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_no_cookie(self, client, mock_db):
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    def test_logout(self, client):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
