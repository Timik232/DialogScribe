import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi.testclient import TestClient

from gigaam_transcriber.models import User
from gigaam_transcriber.database import engine, async_session_factory, Base, get_db
from gigaam_transcriber.template_manager import TemplateManager
from gigaam_transcriber.summarizer import SUMMARY_TEMPLATES


def _make_mock_user():
    return User(
        id="test-user-id",
        email="test@test.com",
        username="testuser",
        password_hash="",
        role="user",
        is_active=True,
    )


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with patch("api.GigaAMTranscriber", return_value=mock_transcriber):
        from api import app
        from gigaam_transcriber.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: _make_mock_user()
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.pop(get_current_user, None)


BUILTIN_TEMPLATES = {
    "meeting": {"label": "Meeting Notes", "system_prompt": "Summarize the meeting."},
    "lecture": {"label": "Lecture", "system_prompt": "Summarize the lecture."},
    "interview": {"label": "Interview", "system_prompt": "Summarize the interview."},
    "general": {"label": "General", "system_prompt": "Provide a general summary."},
}


class TestListTemplates:
    def test_list_builtin_templates(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.list_templates", new_callable=AsyncMock, return_value=[]):
                resp = client.get("/api/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4
        assert all(not t["is_custom"] for t in data)

    def test_list_builtin_and_custom(self, client):
        custom = [{"key": "my-custom", "label": "My Template", "system_prompt": "Custom", "emoji": "", "user_prompt_template": "", "id": "abc", "created_at": "2025-01-01"}]
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.list_templates", new_callable=AsyncMock, return_value=custom):
                resp = client.get("/api/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        slugs = [t["slug"] for t in data]
        assert "meeting" in slugs
        assert "my-custom" in slugs
        meeting = next(t for t in data if t["slug"] == "meeting")
        assert meeting["is_custom"] is False
        custom_t = next(t for t in data if t["slug"] == "my-custom")
        assert custom_t["is_custom"] is True

    def test_unauthenticated(self, client):
        from api import app
        from gigaam_transcriber.auth import get_current_user
        saved = app.dependency_overrides.pop(get_current_user, None)
        try:
            resp = client.get("/api/templates")
        finally:
            app.dependency_overrides[get_current_user] = lambda: _make_mock_user()
        assert resp.status_code in (401, 403)


class TestCreateTemplate:
    def test_create_custom_template(self, client):
        created = {"key": "my-template", "label": "My Template", "system_prompt": "Custom prompt", "emoji": "", "user_prompt_template": "", "id": "1", "created_at": "2025-01-01"}
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.create_template", new_callable=AsyncMock, return_value=created):
                resp = client.post(
                    "/api/templates",
                    json={"name": "My Template", "system_prompt": "Custom prompt"},
                )
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "my-template"
        assert data["name"] == "My Template"
        assert data["is_custom"] is True

    def test_create_duplicate_returns_400(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.create_template", new_callable=AsyncMock, side_effect=ValueError("Template already exists")):
                resp = client.post(
                    "/api/templates",
                    json={"name": "Dup", "system_prompt": "P"},
                )
        assert resp.status_code == 400


class TestUpdateTemplate:
    def test_update_custom_template(self, client):
        existing = {"key": "my-template", "label": "Old Name", "system_prompt": "Old", "emoji": "", "user_prompt_template": "", "id": "1", "created_at": "2025-01-01"}
        updated = {"key": "my-template", "label": "Updated Name", "system_prompt": "New prompt", "emoji": "", "user_prompt_template": "", "id": "1", "created_at": "2025-01-01"}
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.get_template", new_callable=AsyncMock, return_value=existing):
                with patch("routers.templates.TemplateManager.update_template", new_callable=AsyncMock, return_value=updated):
                    resp = client.put(
                        "/api/templates/my-template",
                        json={"name": "Updated Name", "system_prompt": "New prompt"},
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Name"
        assert data["is_custom"] is True

    def test_protect_builtin_template(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            resp = client.put(
                "/api/templates/meeting",
                json={"name": "Hacked"},
            )
        assert resp.status_code == 403
        assert "built-in" in resp.json()["detail"].lower()

    def test_update_nonexistent_template(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.get_template", new_callable=AsyncMock, return_value=None):
                resp = client.put(
                    "/api/templates/nonexistent",
                    json={"name": "X"},
                )
        assert resp.status_code == 404


class TestDeleteTemplate:
    def test_delete_custom_template(self, client):
        existing = {"key": "to-delete", "label": "Delete Me", "system_prompt": "P", "emoji": "", "user_prompt_template": "", "id": "3", "created_at": "2025-01-01"}
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.get_template", new_callable=AsyncMock, return_value=existing):
                with patch("routers.templates.TemplateManager.delete_template", new_callable=AsyncMock):
                    resp = client.delete("/api/templates/to-delete")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["detail"].lower()

    def test_protect_builtin_delete(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            resp = client.delete("/api/templates/lecture")
        assert resp.status_code == 403
        assert "built-in" in resp.json()["detail"].lower()

    def test_delete_nonexistent(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.delete_template", new_callable=AsyncMock, side_effect=ValueError("not found")):
                resp = client.delete("/api/templates/nonexistent")
        assert resp.status_code == 404


class TestExportTemplates:
    def test_export_all(self, client):
        export_data = {"version": 1, "app": "dialogscribe", "exported_at": "2025-01-01T00:00:00", "templates": [{"label": "T", "system_prompt": "P", "emoji": "", "user_prompt_template": ""}]}
        with patch("routers.templates.TemplateManager.export_all_templates", new_callable=AsyncMock, return_value=export_data):
            resp = client.post("/api/templates/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert len(data["templates"]) == 1

    def test_export_single(self, client):
        existing = {"key": "my-t", "label": "My T", "system_prompt": "P", "emoji": "", "user_prompt_template": "", "id": "1", "created_at": "2025-01-01"}
        export_data = {"version": 1, "app": "dialogscribe", "exported_at": "2025-01-01T00:00:00", "templates": [{"label": "My T", "system_prompt": "P", "emoji": "", "user_prompt_template": ""}]}
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.get_template", new_callable=AsyncMock, return_value=existing):
                with patch("routers.templates.TemplateManager.export_template", new_callable=AsyncMock, return_value=export_data):
                    resp = client.post("/api/templates/export/my-t")
        assert resp.status_code == 200

    def test_export_single_builtin_forbidden(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            resp = client.post("/api/templates/export/meeting")
        assert resp.status_code == 403

    def test_export_single_not_found(self, client):
        with patch("routers.templates.SUMMARY_TEMPLATES", BUILTIN_TEMPLATES):
            with patch("routers.templates.TemplateManager.export_template", new_callable=AsyncMock, return_value=None):
                resp = client.post("/api/templates/export/custom-nope")
        assert resp.status_code == 404


class TestImportTemplates:
    def test_import_success(self, client):
        report = {"imported": 2, "skipped": 0, "errors": []}
        with patch("routers.templates.TemplateManager.import_templates", new_callable=AsyncMock, return_value=report):
            resp = client.post(
                "/api/templates/import",
                json={"templates_data": {"templates": [{"label": "A", "system_prompt": "P"}]}},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2

    def test_import_with_errors(self, client):
        report = {"imported": 0, "skipped": 0, "errors": ["bad data"]}
        with patch("routers.templates.TemplateManager.import_templates", new_callable=AsyncMock, return_value=report):
            resp = client.post(
                "/api/templates/import",
                json={"templates_data": {}},
            )
        assert resp.status_code == 200
        assert resp.json()["errors"] == ["bad data"]
