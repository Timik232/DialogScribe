from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.auth import create_access_token, create_refresh_token, get_admin_user, get_current_user
from gigaam_transcriber.models import UsageEvent, User, UserLimit


def _make_user(id="user-1", role="user", is_active=True):
    return User(id=id, email="user@test.com", username="testuser", password_hash="", role=role, is_active=is_active)


def _make_admin():
    return User(id="admin-1", email="admin@test.com", username="admin", password_hash="", role="admin", is_active=True)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def client(mock_db):
    mock_transcriber = MagicMock()
    with patch("api.GigaAMTranscriber", return_value=mock_transcriber):
        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_current_user] = lambda: _make_user()
        app.dependency_overrides[get_admin_user] = lambda: _make_admin()

        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


class TestRegisterLoginFlow:
    def test_register_login_me(self, client, mock_db):
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=lambda: None)

        with patch("routers.auth.hash_password", return_value="hashed"):
            resp = client.post("/api/auth/register", json={
                "email": "new@test.com",
                "username": "newuser",
                "password": "secret12345",
            })
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "newuser"

        fake_user = _make_user()
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=lambda: fake_user)
        with patch("routers.auth.verify_password", return_value=True):
            resp = client.post("/api/auth/login", json={
                "login": "new@test.com",
                "password": "secret12345",
            })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

        token = resp.json()["access_token"]
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"


class TestTokenRefreshFlow:
    def test_refresh_returns_new_access_token(self, client, mock_db):
        user = _make_user()
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=lambda: user)

        refresh_token = create_refresh_token(user.id)
        resp = client.post("/api/auth/refresh", cookies={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_missing_cookie_returns_401(self, client, mock_db):
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401


class TestLogoutFlow:
    def test_logout_clears_cookie(self, client, mock_db):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out"


class TestUsageTracking:
    def test_usage_me_endpoint(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(event_type="transcription_minutes", total=8.5, count=2),
        ]
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/api/usage/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "monthly"
        assert len(data["stats"]) == 1
        assert data["stats"][0]["event_type"] == "transcription_minutes"
        assert data["stats"][0]["total"] == 8.5

    def test_usage_me_daily_period(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/api/usage/me?period=daily")
        assert resp.status_code == 200
        assert resp.json()["period"] == "daily"


class TestUsageLimitsEnforcement:
    def test_limit_exceeded_returns_429(self, client, mock_db):
        from fastapi import HTTPException
        with patch("routers.transcription.check_limit", new_callable=AsyncMock, side_effect=HTTPException(status_code=429, detail="Limit exceeded")):
            resp = client.post("/api/transcribe", files={"file": ("test.wav", b"fake", "audio/wav")})
            assert resp.status_code == 429

    def test_limit_not_exceeded_passes(self, client, mock_db):
        with patch("routers.transcription.check_limit", new_callable=AsyncMock):
            with patch("routers.transcription.track_usage", new_callable=AsyncMock):
                mock_transcriber = MagicMock()
                mock_transcriber.transcribe_file.return_value = MagicMock(
                    segments=[], duration=0, text="", language="ru",
                )
                with patch("api.app.state.transcriber", mock_transcriber):
                    resp = client.post("/api/transcribe", files={"file": ("test.wav", b"fake", "audio/wav")})
                    assert resp.status_code == 200


class TestAdminEndpoints:
    def test_list_users(self, client, mock_db):
        fake_user = _make_user()
        mock_db.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [fake_user]))

        with patch("gigaam_transcriber.usage.get_usage_stats", new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/admin/users")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data

    def test_patch_user_activate_deactivate(self, client, mock_db):
        fake_user = _make_user()
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=lambda: fake_user)

        resp = client.patch("/api/admin/users/user-1", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_put_user_limit(self, client, mock_db):
        fake_user = _make_user()
        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(scalar_one_or_none=lambda: fake_user)
            return MagicMock(scalar_one_or_none=lambda: None)

        mock_db.execute = mock_execute

        resp = client.put("/api/admin/users/user-1/limits", json={
            "limit_type": "transcription_minutes",
            "max_value": 100.0,
            "period": "monthly",
            "enabled": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit_type"] == "transcription_minutes"
        assert data["max_value"] == 100.0
        assert data["enabled"] is True

    def test_get_user_usage(self, client, mock_db):
        with patch("gigaam_transcriber.usage.get_usage_stats", new_callable=AsyncMock, return_value=[
            {"event_type": "llm_call", "total": 5.0, "count": 5},
        ]):
            resp = client.get("/api/admin/users/user-1/usage?period=monthly")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "monthly"
        assert len(data["stats"]) == 1


class TestAdminEnablesLimitThenUserHits429:
    def test_full_flow(self, client, mock_db):
        fake_user = _make_user()
        fake_limit = MagicMock(limit_type="transcription_minutes", max_value=0.0, period="monthly", enabled=True, id="limit-1")
        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(scalar_one_or_none=lambda: fake_user)
            return MagicMock(scalar_one_or_none=lambda: fake_limit)

        mock_db.execute = mock_execute

        resp = client.put("/api/admin/users/user-1/limits", json={
            "limit_type": "transcription_minutes",
            "max_value": 0.0,
            "period": "monthly",
            "enabled": True,
        })
        assert resp.status_code == 200

        from fastapi import HTTPException
        with patch("routers.transcription.check_limit", new_callable=AsyncMock, side_effect=HTTPException(status_code=429, detail="Limit exceeded")):
            resp = client.post("/api/transcribe", files={"file": ("test.wav", b"fake", "audio/wav")})
            assert resp.status_code == 429


class TestProtectedEndpointsRequireAuth:
    def test_unauthenticated_get_usage_returns_401(self):
        mock_transcriber = MagicMock()
        with patch("api.GigaAMTranscriber", return_value=mock_transcriber):
            from api import app
            from gigaam_transcriber.auth import get_current_user
            from gigaam_transcriber.database import get_db

            mock_db = AsyncMock()
            app.dependency_overrides[get_db] = lambda: mock_db
            app.dependency_overrides.pop(get_current_user, None)

            with TestClient(app) as c:
                resp = c.get("/api/usage/me")
                assert resp.status_code == 403 or resp.status_code == 401
            app.dependency_overrides.clear()
