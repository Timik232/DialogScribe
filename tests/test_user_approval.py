from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.admin import router as admin_router
from gigaam_transcriber.auth import get_admin_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db


@pytest.fixture
def admin_user():
    user = MagicMock(spec=User)
    user.id = "admin-id"
    user.email = "admin@test.com"
    user.username = "admin"
    user.role = "admin"
    user.is_active = True
    user.approved_at = datetime(2025, 1, 1)
    return user


@pytest.fixture
def app(mock_db, admin_user):
    _app = FastAPI()
    _app.include_router(admin_router)
    _app.dependency_overrides[get_admin_user] = lambda: admin_user
    _app.dependency_overrides[get_db] = lambda: mock_db
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


def _make_pending_user(user_id="pending-id", username="pending_user"):
    user = MagicMock(spec=User)
    user.id = user_id
    user.email = f"{username}@test.com"
    user.username = username
    user.role = "user"
    user.is_active = False
    user.approved_at = None
    user.approved_by = None
    user.created_at = datetime(2025, 6, 1)
    return user


class TestApproveUser:
    def test_approve_pending_user(self, client, mock_db, admin_user):
        pending = _make_pending_user()

        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: pending))
        resp = client.patch(f"/api/admin/users/{pending.id}/approve")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True
        assert data["approved_at"] is not None

    def test_approve_nonexistent_user(self, client, mock_db, admin_user):
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        resp = client.patch("/api/admin/users/nonexistent/approve")

        assert resp.status_code == 404


class TestRejectUser:
    def test_reject_pending_user(self, client, mock_db, admin_user):
        pending = _make_pending_user()

        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: pending))
        resp = client.delete(f"/api/admin/users/{pending.id}")

        assert resp.status_code == 200

    def test_reject_active_user_forbidden(self, client, mock_db, admin_user):
        active_user = MagicMock(spec=User)
        active_user.id = "active-id"
        active_user.is_active = True

        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: active_user))
        resp = client.delete("/api/admin/users/active-id")

        assert resp.status_code == 400

    def test_reject_nonexistent_user(self, client, mock_db, admin_user):
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        resp = client.delete("/api/admin/users/nonexistent")

        assert resp.status_code == 404


class TestListUsersFilters:
    def test_search_by_email(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/users?search=test@test.com")

        assert resp.status_code == 200

    def test_filter_pending(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/users?status=pending")

        assert resp.status_code == 200

    def test_filter_active(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/users?status=active")

        assert resp.status_code == 200

    def test_filter_disabled(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/users?status=disabled")

        assert resp.status_code == 200
