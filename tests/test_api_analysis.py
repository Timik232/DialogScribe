from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_mock_user, setup_auth_override, clear_auth_override


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.analysis.check_limit"),
        patch("routers.analysis.track_usage"),
    ):
        from api import app

        setup_auth_override(app)
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


def _mock_llm(api_key="test-key", model="default-model", base_url="http://test"):
    mock = MagicMock()
    mock.config.api_key = api_key
    mock.config.model = model
    mock.config.base_url = base_url
    return mock


class TestSummary:
    def test_successful_summary(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_summary", new=AsyncMock(return_value="# Summary\n\nKey points.")),
            patch("routers.analysis.summary_to_html", return_value="<h1>Summary</h1>"),
        ):
            resp = client.post(
                "/api/summary",
                json={"text": "Some long text", "template_key": "general"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary_markdown"] == "# Summary\n\nKey points."
        assert data["summary_html"] == "<h1>Summary</h1>"

    def test_summary_with_custom_model(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_summary", new=AsyncMock(return_value="Result")),
            patch("routers.analysis.summary_to_html", return_value="<p>Result</p>"),
        ):
            resp = client.post(
                "/api/summary",
                json={"text": "Text", "model": "gpt-4"},
            )

        assert resp.status_code == 200
        mock_llm.update_config.assert_called_once()

    def test_llm_not_configured(self, client):
        mock_llm = _mock_llm(api_key="")

        with patch("routers.analysis.llm_client", mock_llm):
            resp = client.post(
                "/api/summary",
                json={"text": "Some text"},
            )

        assert resp.status_code == 503
        assert "LLM_API_KEY" in resp.json()["detail"]

    def test_unauthenticated(self, client):
        from api import app
        from gigaam_transcriber.auth import get_current_user

        clear_auth_override(app)
        try:
            resp = client.post(
                "/api/summary",
                json={"text": "test"},
            )
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)

    def test_summary_value_error(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_summary", new=AsyncMock(side_effect=ValueError("Bad template"))),
        ):
            resp = client.post(
                "/api/summary",
                json={"text": "Text", "template_key": "invalid"},
            )

        assert resp.status_code == 400

    def test_summary_connection_error(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_summary", new=AsyncMock(side_effect=ConnectionError("Timeout"))),
        ):
            resp = client.post(
                "/api/summary",
                json={"text": "Text"},
            )

        assert resp.status_code == 502


class TestMindmap:
    def test_successful_mindmap(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_mindmap_markdown", return_value="# Root\n## Branch"),
            patch("routers.analysis.render_mindmap_html", return_value='<iframe src="/mindmap/test" width="100%" height="500"></iframe>'),
        ):
            resp = client.post(
                "/api/mindmap",
                json={"text": "Some text about topics"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mindmap_markdown"] == "# Root\n## Branch"
        assert "mindmap_uid" in data
        assert len(data["mindmap_uid"]) == 12
        assert "mindmap_html" in data
        assert "<iframe" in data["mindmap_html"]

    def test_mindmap_llm_not_configured(self, client):
        mock_llm = _mock_llm(api_key="")

        with patch("routers.analysis.llm_client", mock_llm):
            resp = client.post(
                "/api/mindmap",
                json={"text": "test"},
            )

        assert resp.status_code == 503

    def test_unauthenticated(self, client):
        from api import app

        clear_auth_override(app)
        try:
            resp = client.post(
                "/api/mindmap",
                json={"text": "test"},
            )
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)


class TestModels:
    def test_list_models(self, client):
        with patch(
            "routers.analysis.get_available_models",
            return_value=["gpt-4", "claude-3", "mistral-large"],
        ):
            resp = client.get("/api/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 3
        assert data["models"][0]["id"] == "gpt-4"
        assert data["models"][0]["name"] == "gpt-4"

    def test_list_models_empty(self, client):
        with patch("routers.analysis.get_available_models", return_value=[]):
            resp = client.get("/api/models")

        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_unauthenticated(self, client):
        from api import app

        clear_auth_override(app)
        try:
            resp = client.get("/api/models")
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)


class TestInsights:
    def test_successful_insights(self, client):
        mock_llm = _mock_llm()
        action_items_response = {
            "action_items": [
                {"task": "Подготовить отчёт", "assignee": "Анна", "deadline": "пятница", "priority": "high"}
            ],
            "decisions": [{"decision": "Утвердить бюджет", "context": "Согласовано на совещании"}],
        }
        suggested_steps_response = {
            "suggested_steps": [
                {"step": "Отправить follow-up", "reason": "Закрепить решение", "category": "followup"}
            ]
        }

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.extract_action_items", return_value=action_items_response),
            patch("routers.analysis.generate_suggested_steps", return_value=suggested_steps_response),
        ):
            resp = client.post(
                "/api/insights",
                json={"text": "На совещании решили..."},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["action_items"]) == 1
        assert data["action_items"][0]["task"] == "Подготовить отчёт"
        assert len(data["decisions"]) == 1
        assert len(data["suggested_steps"]) == 1

    def test_action_items_only(self, client):
        mock_llm = _mock_llm()
        action_items_response = {"action_items": [], "decisions": []}

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.extract_action_items", return_value=action_items_response),
        ):
            resp = client.post(
                "/api/insights",
                json={"text": "Text", "include_action_items": True, "include_suggested_steps": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "action_items" in data
        assert "suggested_steps" not in data

    def test_suggested_steps_only(self, client):
        mock_llm = _mock_llm()
        steps_response = {"suggested_steps": []}

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.generate_suggested_steps", return_value=steps_response),
        ):
            resp = client.post(
                "/api/insights",
                json={"text": "Text", "include_action_items": False, "include_suggested_steps": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "suggested_steps" in data
        assert "action_items" not in data

    def test_insights_llm_not_configured(self, client):
        mock_llm = _mock_llm(api_key="")

        with patch("routers.analysis.llm_client", mock_llm):
            resp = client.post("/api/insights", json={"text": "Text"})

        assert resp.status_code == 503

    def test_insights_unauthenticated(self, client):
        from api import app

        clear_auth_override(app)
        try:
            resp = client.post("/api/insights", json={"text": "test"})
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)

    def test_insights_connection_error(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.extract_action_items", side_effect=ConnectionError("Timeout")),
        ):
            resp = client.post("/api/insights", json={"text": "Text"})

        assert resp.status_code == 502


class TestChat:
    def test_successful_chat(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.chat_with_transcript", return_value={"answer": "Ключевое решение — запуск проекта."}),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "text": "Спикер 1: Мы решили запустить проект.",
                    "messages": [{"role": "user", "content": "Какие ключевые решения были приняты?"}],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Ключевое решение — запуск проекта."

    def test_empty_messages_returns_400(self, client):
        mock_llm = _mock_llm()

        with patch("routers.analysis.llm_client", mock_llm):
            resp = client.post(
                "/api/chat",
                json={"text": "Some text", "messages": []},
            )

        assert resp.status_code == 400

    def test_chat_with_model_selection(self, client):
        mock_llm = _mock_llm()

        with (
            patch("routers.analysis.llm_client", mock_llm),
            patch("routers.analysis.chat_with_transcript", return_value={"answer": "Ответ"}),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "text": "Text",
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "Question"}],
                },
            )

        assert resp.status_code == 200
        mock_llm.update_config.assert_called_once()

    def test_chat_llm_not_configured(self, client):
        mock_llm = _mock_llm(api_key="")

        with patch("routers.analysis.llm_client", mock_llm):
            resp = client.post(
                "/api/chat",
                json={"text": "Text", "messages": [{"role": "user", "content": "Q"}]},
            )

        assert resp.status_code == 503

    def test_chat_unauthenticated(self, client):
        from api import app

        clear_auth_override(app)
        try:
            resp = client.post(
                "/api/chat",
                json={"text": "test", "messages": [{"role": "user", "content": "Q"}]},
            )
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)
