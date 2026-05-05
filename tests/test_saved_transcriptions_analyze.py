"""Tests for POST /api/saved-transcriptions/{transcription_id}/analyze endpoint."""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from tests.conftest import make_mock_user, setup_auth_override, clear_auth_override


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_transcription(
    id="test-st-id",
    user_id="test-user-id",
    full_text="Test text for analysis",
    analysis_text=None,
):
    t = MagicMock()
    t.id = id
    t.user_id = user_id
    t.full_text = full_text
    t.analysis_text = analysis_text
    t.title = "Test"
    t.segments_json = []
    t.speaker_names = {}
    t.duration = 10.0
    t.language = "ru"
    t.share_id = None
    t.created_at = "2025-01-01T00:00:00"
    t.updated_at = "2025-01-01T00:00:00"
    return t


def _mock_llm(api_key="test-key", model="default-model", base_url="http://test"):
    mock = MagicMock()
    mock.config.api_key = api_key
    mock.config.model = model
    mock.config.base_url = base_url
    return mock


def _make_db(mock_transcription=None):
    """Create a mock AsyncSession that returns the given transcription on execute."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = mock_transcription
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _analysis_patches(mock_llm):
    """Return list of (target, mock) pairs for patching analysis functions."""
    return [
        ("routers.saved_transcriptions.llm_client", mock_llm),
        ("routers.saved_transcriptions.generate_summary", AsyncMock(return_value="# Сводка\nTest summary")),
        ("routers.saved_transcriptions.generate_mindmap_markdown", MagicMock(return_value="# Интеллект-карта\nTest mindmap")),
        ("routers.saved_transcriptions.extract_action_items", MagicMock(return_value={"action_items": ["Task A"], "decisions": ["Decision B"]})),
        ("routers.saved_transcriptions.generate_suggested_steps", MagicMock(return_value={"suggested_steps": ["Step 1"]})),
    ]


def _apply_patches(stack, patches):
    """Apply a list of (target, mock) pairs via ExitStack, returning the mocks."""
    mocks = []
    for target, mock_val in patches:
        if isinstance(mock_val, AsyncMock):
            p = stack.enter_context(patch(target, new=mock_val))
        else:
            p = stack.enter_context(patch(target, new=mock_val))
        mocks.append(p)
    return mocks


# ── Module-scoped client fixture ─────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Module-scoped test client with mocked transcriber, check_limit, track_usage."""
    mock_transcriber = MagicMock()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.saved_transcriptions.check_limit", new_callable=AsyncMock),
        patch("routers.saved_transcriptions.track_usage", new_callable=AsyncMock),
    ):
        from api import app

        setup_auth_override(app)

        # Override DB dependency with a per-test configurable mock
        from gigaam_transcriber.database import get_db

        mock_db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: mock_db

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()


# ── Tests ────────────────────────────────────────────────────────────


class TestAnalyzeEndpoint:
    """9 test cases for the saved-transcriptions analyze endpoint."""

    def test_analyze_success(self, client):
        """POST /analyze → 200 with analysis_text containing all 4 section headers."""
        mock_llm = _mock_llm()
        transcription = _mock_transcription()
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with ExitStack() as stack:
                _apply_patches(stack, _analysis_patches(mock_llm))
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 200
        data = resp.json()
        assert "analysis_text" in data
        assert data["analysis_text"] is not None
        assert "# Сводка" in data["analysis_text"]
        assert "# Интеллект-карта" in data["analysis_text"]
        assert "# Задачи и решения" in data["analysis_text"]
        assert "# Предлагаемые шаги" in data["analysis_text"]

    def test_analyze_404_not_found(self, client):
        """Non-existent transcription ID → 404."""
        mock_llm = _mock_llm()
        db = _make_db(None)  # No transcription found

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with patch("routers.saved_transcriptions.llm_client", mock_llm):
                resp = client.post("/api/saved-transcriptions/nonexistent-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 404

    def test_analyze_400_empty_text(self, client):
        """Transcription with empty full_text → 400."""
        mock_llm = _mock_llm()
        transcription = _mock_transcription(full_text="   ")
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with patch("routers.saved_transcriptions.llm_client", mock_llm):
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 400

    def test_analyze_429_limit_exceeded(self, client):
        """When check_limit raises HTTPException(429) → 429 propagated."""
        mock_llm = _mock_llm()
        transcription = _mock_transcription()
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with (
                patch("routers.saved_transcriptions.llm_client", mock_llm),
                patch(
                    "routers.saved_transcriptions.check_limit",
                    new=AsyncMock(side_effect=HTTPException(status_code=429, detail="Limit exceeded")),
                ),
            ):
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 429

    def test_analyze_503_no_llm_key(self, client):
        """llm_client.config.api_key is empty → 503."""
        mock_llm = _mock_llm(api_key="")
        transcription = _mock_transcription()
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with patch("routers.saved_transcriptions.llm_client", mock_llm):
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 503
        assert "LLM_API_KEY" in resp.json()["detail"]

    def test_analyze_401_unauthenticated(self, client):
        """No auth → 401 or 403."""
        from api import app

        clear_auth_override(app)
        try:
            resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)

    def test_analyze_stores_result(self, client):
        """Verify analysis_text field is in response (proves persistence)."""
        mock_llm = _mock_llm()
        transcription = _mock_transcription()
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with ExitStack() as stack:
                _apply_patches(stack, _analysis_patches(mock_llm))
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 200
        data = resp.json()
        assert "analysis_text" in data
        assert data["analysis_text"] is not None
        db.commit.assert_called()

    def test_analyze_overwrites_existing(self, client):
        """Re-analysis returns new result (overwrites previous analysis_text)."""
        mock_llm = _mock_llm()
        # Transcription already has analysis_text
        transcription = _mock_transcription(analysis_text="OLD ANALYSIS")
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with ExitStack() as stack:
                _apply_patches(stack, _analysis_patches(mock_llm))
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis_text"] is not None
        assert "OLD ANALYSIS" not in data["analysis_text"]
        assert "# Сводка" in data["analysis_text"]

    def test_analyze_partial_failure(self, client):
        """One analysis function raises Exception → 500, no analysis_text in response."""
        mock_llm = _mock_llm()
        transcription = _mock_transcription()
        db = _make_db(transcription)

        from api import app
        from gigaam_transcriber.database import get_db

        app.dependency_overrides[get_db] = lambda: db

        try:
            with (
                patch("routers.saved_transcriptions.llm_client", mock_llm),
                patch(
                    "routers.saved_transcriptions.generate_summary",
                    new=AsyncMock(return_value="# Сводка\nTest summary"),
                ),
                patch(
                    "routers.saved_transcriptions.generate_mindmap_markdown",
                    side_effect=RuntimeError("LLM service unavailable"),
                ),
            ):
                resp = client.post("/api/saved-transcriptions/test-st-id/analyze")
        finally:
            app.dependency_overrides[get_db] = lambda: AsyncMock()

        assert resp.status_code == 500
        # No analysis_text should be set on the object (all-or-nothing)
        # The transcription's analysis_text should not have been updated
        assert transcription.analysis_text is None or transcription.analysis_text == "OLD ANALYSIS"
