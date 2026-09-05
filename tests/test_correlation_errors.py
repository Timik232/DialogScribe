"""CQ-M9: public errors carry code + correlation id only; logs keep detail.

Raw exception text (stack, provider body, path, secret material) must never
reach analysis/_helpers/autoflow error responses; the correlation id in the
payload matches the X-Correlation-ID header and server-side log records.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_mock_user, setup_auth_override, clear_auth_override


@pytest.fixture()
def client():
    import api as api_mod

    app = api_mod.app
    setup_auth_override(app)
    yield TestClient(app)
    app.dependency_overrides.clear()
    clear_auth_override(app)


def test_correlation_header_on_every_response(client):
    resp = client.get("/api/models")
    assert resp.headers.get("X-Correlation-ID")
    first = resp.headers["X-Correlation-ID"]
    resp2 = client.get("/api/models")
    assert resp2.headers["X-Correlation-ID"] != first


def test_analysis_500_sanitized_with_correlation(client):
    from routers import analysis as mod

    with patch.object(mod, "get_available_models", side_effect=RuntimeError(
        "secret-token /var/lib/leak.txt boom"
    )):
        resp = client.get("/api/models")

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert set(detail["error"].keys()) == {"code", "message", "correlation_id"}
    assert detail["error"]["code"] == "internal_error"
    assert "secret-token" not in resp.text
    assert "/var/lib" not in resp.text
    assert detail["error"]["correlation_id"] == resp.headers["X-Correlation-ID"]


def test_analysis_connection_error_sanitized(client):
    from routers import analysis as mod

    user = make_mock_user()
    with patch.object(mod, "llm_client") as mock_llm, patch.object(
        mod, "get_current_user", return_value=user
    ):
        mock_llm.config.api_key = "k"
        mock_llm.call = MagicMock(side_effect=ConnectionError("upstream body: sk-123"))
        resp = client.post("/api/summary", json={"text": "x"})

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "upstream_unavailable"
    assert "sk-123" not in resp.text
    assert detail["error"]["correlation_id"]


def test_logs_carry_correlation_context(client):
    from routers.correlation import CorrelationLogFilter

    captured = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = CaptureHandler()
    handler.addFilter(CorrelationLogFilter())
    logging.getLogger().addHandler(handler)
    try:
        from routers import analysis as mod

        with patch.object(mod, "get_available_models", side_effect=RuntimeError(
            "detail-with-secret"
        )):
            resp = client.get("/api/models")
        cid = resp.json()["detail"]["error"]["correlation_id"]
    finally:
        logging.getLogger().removeHandler(handler)

    matching = [r for r in captured if r.getMessage() and cid in str(r.getMessage())]
    assert matching, "expected a log record carrying the response correlation id"
    for record in matching:
        assert getattr(record, "correlation_id", None) == cid


def test_v1_transcription_error_sanitized():
    import api as api_mod
    from routers._helpers import _handle_transcription_exception
    from gigaam_transcriber.exceptions import ASRError

    exc = ASRError("HTTP 429: {\"message\": \"Rate limit sk-provider-key\"}")
    http_exc = _handle_transcription_exception(exc)
    detail = http_exc.detail
    assert detail["error"]["message"] == "Speech recognition provider error"
    assert "sk-provider-key" not in str(detail)
    assert "HTTP 429" not in str(detail)
    assert detail["correlation_id"]
