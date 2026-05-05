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


class TestStatsOverview:
    def test_stats_overview(self, client, mock_db, admin_user):
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar=MagicMock(return_value=10)),
            MagicMock(scalar=MagicMock(return_value=8)),
            MagicMock(scalar=MagicMock(return_value=2)),
            MagicMock(one=MagicMock(return_value=MagicMock(total_value=100.0, total_count=50))),
        ])
        resp = client.get("/api/admin/stats/overview")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_users"] == 10
        assert data["active_users"] == 8
        assert data["pending_users"] == 2
        assert data["total_usage_count"] == 50


class TestStatsTimeseries:
    def test_timeseries_default_days(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/stats/timeseries")

        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert data["days"] == 14

    def test_timeseries_custom_days(self, client, mock_db, admin_user):
        mock_result = MagicMock()
        mock_result.all.return_value = []

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/stats/timeseries?days=7")

        assert resp.status_code == 200
        data = resp.json()
        assert data["days"] == 7

    def test_timeseries_with_data(self, client, mock_db, admin_user):
        row = MagicMock()
        row.date = "2025-06-01"
        row.event_type = "transcription_minutes"
        row.total = 42.0
        row.count = 10

        mock_result = MagicMock()
        mock_result.all.return_value = [row]

        mock_db.execute = AsyncMock(return_value=mock_result)
        resp = client.get("/api/admin/stats/timeseries")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["event_type"] == "transcription_minutes"
        assert data["data"][0]["total"] == 42.0
