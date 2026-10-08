"""Task 15: export temp-file lifecycle and sanitized exporter failures."""

import asyncio
import os
import tempfile
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

INSIGHTS_BODY = {
    "action_items": [{"task": "call back", "priority": "high"}],
    "decisions": [],
    "suggested_steps": [],
    "format": "docx",
}


class _TempTracker:
    def __init__(self):
        self.paths = []
        self._real = tempfile.mkstemp

    def __call__(self, *args, **kwargs):
        fd, path = self._real(*args, **kwargs)
        self.paths.append(path)
        return fd, path


@pytest.fixture()
def tracked_temps():
    tracker = _TempTracker()
    with patch("routers.exports.tempfile.mkstemp", tracker):
        yield tracker


class TestExporterFailureCleanup:
    def test_docx_exporter_failure_returns_502_and_cleans(self, client, tracked_temps):
        def _boom(result, path):
            raise RuntimeError(f"docx blew up on {path}")

        with patch("routers.exports.export_docx_transcription", side_effect=_boom):
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": "docx", "filename": "t"},
            )

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        assert "/tmp" not in detail
        assert "blew up" not in detail
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_pdf_exporter_failure_returns_502_and_cleans(self, client, tracked_temps):
        with patch(
            "routers.exports.export_pdf_transcription",
            side_effect=OSError("disk exploded while writing /var/secret"),
        ):
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": "pdf", "filename": "t"},
            )

        assert resp.status_code == 502
        assert "/var/secret" not in resp.text
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_insights_docx_failure_returns_502_and_cleans(self, client, tracked_temps):
        with patch(
            "routers.exports.export_docx_insights",
            side_effect=RuntimeError("weasy internal /usr/lib leak"),
        ):
            resp = client.post("/api/export-insights", json=INSIGHTS_BODY)

        assert resp.status_code == 502
        assert "/usr/lib" not in resp.text
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_insights_txt_write_failure_cleans(self, client, tracked_temps, tmp_path):
        with patch(
            "gigaam_transcriber.insights.export_insights_txt",
            return_value="task: call back",
        ):
            real_write = Path.write_text

            def failing_write(self, *a, **kw):
                if str(self).startswith(tempfile.gettempdir()):
                    raise OSError("no space left on device")
                return real_write(self, *a, **kw)

            with patch.object(Path, "write_text", failing_write):
                resp = client.post(
                    "/api/export-insights",
                    json={**INSIGHTS_BODY, "format": "txt"},
                )

        assert resp.status_code == 502
        assert "no space" not in resp.text
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_malformed_payload_returns_422_without_tempfile(self, client, tracked_temps):
        resp = client.post(
            "/api/export",
            json={"data": {"segments": []}, "format": "txt", "filename": "t"},
        )
        assert resp.status_code == 422
        assert tracked_temps.paths == []


class TestSuccessfulExportLifecycle:
    @pytest.mark.parametrize("fmt", ["json", "txt", "srt", "vtt", "docx", "pdf"])
    def test_full_body_readable_then_deleted(self, client, tracked_temps, fmt):
        def _fake_create(result, path):
            Path(path).write_bytes(b"fake export bytes " * 10)
            return path

        patch_target = (
            "routers.exports.export_docx_transcription"
            if fmt == "docx"
            else "routers.exports.export_pdf_transcription"
        )
        with patch(patch_target, side_effect=_fake_create):
            resp = client.post(
                "/api/export",
                json={"data": SAMPLE_DATA, "format": fmt, "filename": "t"},
            )

        assert resp.status_code == 200
        assert resp.content, f"{fmt} body must be fully readable"
        if fmt in ("docx", "pdf"):
            assert resp.content == b"fake export bytes " * 10
        else:
            assert b"Hello world" in resp.content
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_insights_txt_full_body_then_deleted(self, client, tracked_temps):
        with patch(
            "gigaam_transcriber.insights.export_insights_txt",
            return_value="task: call back",
        ):
            resp = client.post(
                "/api/export-insights",
                json={**INSIGHTS_BODY, "format": "txt"},
            )
        assert resp.status_code == 200
        assert "call back" in resp.text
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)

    def test_insights_docx_full_body_then_deleted(self, client, tracked_temps):
        def _fake_create(items, decisions, steps, path):
            Path(path).write_bytes(b"docx-insights-bytes")
            return path

        with patch("routers.exports.export_docx_insights", side_effect=_fake_create):
            resp = client.post("/api/export-insights", json=INSIGHTS_BODY)
        assert resp.status_code == 200
        assert resp.content == b"docx-insights-bytes"
        assert all(not Path(p).exists() for p in tracked_temps.paths)


class TestAbortedStreaming:
    def _run_response(self, payload_size: int, abort_after_chunks: int | None):
        from routers.exports import _ExportFileResponse

        fd, name = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        p = Path(name)
        p.write_bytes(b"x" * payload_size)
        readable_during = []
        body_chunks = []
        send_count = {"n": 0}
        error = {"value": None}

        async def send(message):
            if message["type"] != "http.response.body":
                return
            if message.get("more_body"):
                readable_during.append(p.exists())
                send_count["n"] += 1
                if abort_after_chunks is not None and send_count["n"] > abort_after_chunks:
                    error["value"] = ConnectionResetError("client aborted mid-stream")
                    raise error["value"]
            body_chunks.append(message.get("body", b""))

        resp = _ExportFileResponse(path=str(p), cleanup_path=str(p))
        scope = {"type": "http", "method": "GET", "headers": [], "extensions": {}}
        try:
            asyncio.run(resp(scope, None, send))
        except BaseException as exc:  # noqa: BLE001 - re-raised below for assertion
            if error["value"] is None or exc is not error["value"]:
                raise
        return p, readable_during, b"".join(body_chunks)

    def test_file_stays_readable_while_streaming_then_deleted(self):
        p, readable_during, body = self._run_response(300_000, abort_after_chunks=None)
        assert readable_during and all(readable_during)
        assert len(body) == 300_000
        assert not p.exists()

    def test_aborted_client_still_deletes_file(self):
        p, _, _ = self._run_response(300_000, abort_after_chunks=1)
        assert not p.exists()


class TestCancellationDuringGeneration:
    def test_cancelled_request_cleans_tempfile(self, client, tracked_temps):
        def _cancelled(result, path):
            raise asyncio.CancelledError()

        with patch("routers.exports.export_docx_transcription", side_effect=_cancelled):
            # BaseHTTPMiddleware converts the re-raised cancellation into
            # "No response returned" — either way the endpoint must not 502
            # and the tempfile must be gone.
            with pytest.raises((asyncio.CancelledError, RuntimeError)):
                client.post(
                    "/api/export",
                    json={"data": SAMPLE_DATA, "format": "docx", "filename": "t"},
                )
        assert tracked_temps.paths
        assert all(not Path(p).exists() for p in tracked_temps.paths)
