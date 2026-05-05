import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import setup_auth_override, clear_auth_override


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.transcription.check_limit"),
        patch("routers.transcription.track_usage"),
    ):
        from api import app

        setup_auth_override(app)
        with TestClient(app) as c:
            yield c, mock_transcriber
        app.dependency_overrides.clear()


def _audio_file(filename="test.wav", content=b"fake audio"):
    return ("file", (filename, io.BytesIO(content), "audio/wav"))


def _mock_segment(text="Hello", start=0.0, end=1.0, speaker=None, confidence=None):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.speaker = speaker
    seg.confidence = confidence
    return seg


def _mock_result(segments=None, duration=1.0, text="Hello", language="en"):
    result = MagicMock()
    result.segments = segments or [_mock_segment()]
    result.duration = duration
    result.text = text
    result.language = language
    return result


class TestTranscribe:
    def test_successful_transcription(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result(
            segments=[_mock_segment("Hello world", 0.0, 1.5)],
            duration=1.5,
            text="Hello world",
        )

        resp = c.post("/api/transcribe", files=[_audio_file()])

        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Hello world"
        assert data["duration"] == 1.5
        assert len(data["segments"]) == 1
        assert data["segments"][0]["text"] == "Hello world"

    def test_transcription_with_diarization(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result(
            segments=[
                _mock_segment("Hello", 0.0, 1.0, speaker="Speaker 1"),
                _mock_segment("World", 1.0, 2.0, speaker="Speaker 2"),
            ],
            duration=2.0,
            text="Hello World",
        )

        resp = c.post(
            "/api/transcribe",
            files=[_audio_file()],
            data={"diarization_mode": "simple", "language": "en"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["segments"][0]["speaker"] == "Speaker 1"
        call_args = mock_t.transcribe.call_args
        assert call_args.kwargs["diarization"] == "simple"
        assert call_args.kwargs["language"] == "en"

    def test_invalid_file_format(self, client):
        c, _ = client

        resp = c.post(
            "/api/transcribe",
            files=[("file", ("test.xyz", io.BytesIO(b"data"), "application/octet-stream"))],
        )

        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_file_too_large(self, client):
        c, _ = client

        with patch("routers.transcription.MAX_UPLOAD_SIZE_MB", 0):
            resp = c.post(
                "/api/transcribe",
                files=[("file", ("big.wav", io.BytesIO(b"x" * 1024), "audio/wav"))],
            )

        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_unauthenticated(self, client):
        c, _ = client
        from api import app

        clear_auth_override(app)
        try:
            resp = c.post("/api/transcribe", files=[_audio_file()])
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)


class TestTranscribeMicrophone:
    def test_successful_microphone(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result(
            text="Mic input", duration=3.0, language="en"
        )

        resp = c.post("/api/transcribe/microphone", files=[_audio_file("mic.wav")])

        assert resp.status_code == 200
        assert resp.json()["text"] == "Mic input"

    def test_unauthenticated(self, client):
        c, _ = client
        from api import app

        clear_auth_override(app)
        try:
            resp = c.post("/api/transcribe/microphone", files=[_audio_file()])
        finally:
            setup_auth_override(app)

        assert resp.status_code in (401, 403)
