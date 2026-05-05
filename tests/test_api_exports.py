import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.models import User


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
        app.dependency_overrides.clear()


SAMPLE_DATA = {
    "text": "Hello world",
    "segments": [
        {"text": "Hello world", "start": 0.0, "end": 1.5}
    ],
    "duration": 1.5,
    "language": "en",
    "model_name": "test-model",
    "processing_time": 0.5,
    "metadata": {},
}


class TestTextExportFormats:
    @pytest.mark.parametrize("fmt,expected_mime", [
        ("json", "application/json"),
        ("txt", "text/plain"),
        ("srt", "text/plain"),
        ("vtt", "text/plain"),
    ])
    def test_text_format_export(self, client, fmt, expected_mime):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": fmt, "filename": "test"},
        )

        assert resp.status_code == 200
        assert expected_mime in resp.headers["content-type"]

    def test_json_export_contains_text(self, client):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": "json", "filename": "test"},
        )

        assert resp.status_code == 200
        body = resp.text
        assert "Hello world" in body

    def test_srt_export_has_timestamps(self, client):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": "srt", "filename": "test"},
        )

        assert resp.status_code == 200
        assert "-->" in resp.text

    def test_vtt_export_has_header(self, client):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": "vtt", "filename": "test"},
        )

        assert resp.status_code == 200
        assert "WEBVTT" in resp.text


class TestDocxExport:
    def test_docx_export(self, client):
        def _fake_create(result, path):
            Path(path).write_bytes(b"fake docx")
            return path

        with patch("routers.exports.export_docx_transcription", side_effect=_fake_create):
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": "docx", "filename": "test"},
            )

        assert resp.status_code == 200
        assert "openxmlformats" in resp.headers["content-type"]


class TestPdfExport:
    def test_pdf_export(self, client):
        def _fake_create(result, path):
            Path(path).write_bytes(b"fake pdf")
            return path

        with patch("routers.exports.export_pdf_transcription", side_effect=_fake_create):
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": "pdf", "filename": "test"},
            )

        assert resp.status_code == 200
        assert "pdf" in resp.headers["content-type"]


class TestExportEdgeCases:
    def test_invalid_format(self, client):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": "xyz", "filename": "test"},
        )

        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_unauthenticated(self, client):
        from api import app
        from gigaam_transcriber.auth import get_current_user

        saved = app.dependency_overrides.pop(get_current_user, None)
        try:
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": "json", "filename": "test"},
            )
        finally:
            app.dependency_overrides[get_current_user] = lambda: _make_mock_user()

        assert resp.status_code in (401, 403)

    def test_format_case_insensitive(self, client):
        resp = client.post(
            "/api/export",
            json={"data": SAMPLE_DATA, "format": "JSON", "filename": "test"},
        )

        assert resp.status_code == 200
