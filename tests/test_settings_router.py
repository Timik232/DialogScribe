"""Task 12 / CQ-H5: /api/settings router — auth, scoping, enum validation, default."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_mock_user, setup_auth_override


def _collect_route_paths(routes):
    paths = set()
    for route in routes:
        if hasattr(route, "original_router"):
            paths.update(_collect_route_paths(route.original_router.routes))
        elif hasattr(route, "routes"):
            paths.update(_collect_route_paths(route.routes))
        else:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
    return paths


@pytest.fixture(scope="module")
def client():
    from api import app
    from gigaam_transcriber.database import Base, engine

    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())
    setup_auth_override(app)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unique_user(client):
    """Свежий user id на каждый тест: у него нет строки в user_settings."""
    import uuid

    from gigaam_transcriber.auth import get_current_user
    from api import app

    user = make_mock_user()
    user.id = f"settings-{uuid.uuid4()}"
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    setup_auth_override(app)


class TestSettingsRouter:
    def test_get_returns_default_litellm_without_row(self, client, unique_user):
        resp = client.get("/api/settings/asr-provider")

        assert resp.status_code == 200
        assert resp.json() == {"provider": "litellm"}

    def test_put_persists_and_roundtrips(self, client, unique_user):
        resp = client.put("/api/settings/asr-provider", json={"provider": "mistral"})

        assert resp.status_code == 200
        assert resp.json() == {"provider": "mistral"}
        assert client.get("/api/settings/asr-provider").json() == {"provider": "mistral"}

        resp = client.put("/api/settings/asr-provider", json={"provider": "litellm"})
        assert resp.json() == {"provider": "litellm"}

    def test_put_rejects_unknown_provider(self, client, unique_user):
        resp = client.put("/api/settings/asr-provider", json={"provider": "gigaam"})

        assert resp.status_code == 422

    def test_settings_are_user_scoped(self, client, unique_user):
        client.put("/api/settings/asr-provider", json={"provider": "mistral"})

        other = unique_user
        other.id = unique_user.id + "-other"

        resp = client.get("/api/settings/asr-provider")

        assert resp.status_code == 200
        assert resp.json() == {"provider": "litellm"}

    def test_requires_authentication(self, client):
        from gigaam_transcriber.auth import get_current_user
        from api import app

        app.dependency_overrides.pop(get_current_user, None)
        try:
            resp = client.get("/api/settings/asr-provider")
            assert resp.status_code in (401, 403)

            resp = client.put("/api/settings/asr-provider", json={"provider": "mistral"})
            assert resp.status_code in (401, 403)
        finally:
            setup_auth_override(app)

    def test_router_registered_under_api_settings(self, client):
        paths = _collect_route_paths(client.app.routes)

        assert "/api/settings/asr-provider" in paths
