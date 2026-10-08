"""Task 11: bounded upload ingestion, raw-body middleware, chat caps,
model allowlist and the stale-tempfile startup sweep."""

import io
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber import rate_limit
from tests.conftest import make_mock_user

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.transcription.check_limit"),
        patch("routers.transcription.track_usage"),
    ):
        from api import app
        import routers.transcription as transcription_module

        import asyncio

        from gigaam_transcriber.database import Base, engine

        async def _create_tables():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create_tables())

        # Key on the function object the routers actually reference:
        # test_settings_validation reloads gigaam_transcriber.auth, which
        # would otherwise leave this override keyed on a stale function.
        auth_fn = transcription_module.get_current_user
        app.dependency_overrides[auth_fn] = lambda: make_mock_user()
        with TestClient(app) as c:
            yield c, mock_transcriber
        app.dependency_overrides.pop(auth_fn, None)
        app.dependency_overrides.clear()


def _audio(filename="test.wav", size=16):
    return ("file", (filename, io.BytesIO(b"x" * size), "audio/wav"))


def _mock_llm(api_key="k" * 40):
    llm = MagicMock()
    llm.config.api_key = api_key
    return llm


def _mock_result(text="ok", duration=1.0):
    result = MagicMock()
    seg = MagicMock(text=text, start=0.0, end=1.0, speaker=None, confidence=None)
    result.segments = [seg]
    result.text = text
    result.duration = duration
    result.language = "en"
    return result


def _ds_up_files() -> set[str]:
    return {
        entry.name
        for entry in os.scandir(tempfile.gettempdir())
        if entry.name.startswith("ds_up_")
    }



class TestUploadBoundary:
    def test_exact_limit_accepted(self, client, monkeypatch):
        c, mock_t = client
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
        mock_t.transcribe.return_value = _mock_result()

        resp = c.post("/api/transcribe", files=[_audio(size=MB)])

        assert resp.status_code == 200, resp.text
        assert mock_t.transcribe.called

    def test_limit_plus_one_rejected_before_asr_without_orphans(self, client, monkeypatch):
        c, mock_t = client
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
        mock_t.reset_mock()
        before = _ds_up_files()

        resp = c.post("/api/transcribe", files=[_audio(size=MB + 1)])

        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()
        mock_t.transcribe.assert_not_called()
        assert _ds_up_files() == before

    def test_v1_limit_plus_one_openai_error(self, client, monkeypatch):
        c, mock_t = client
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
        from routers._helpers import _verify_auth

        from api import app

        app.dependency_overrides[_verify_auth] = lambda: None
        try:
            mock_t.reset_mock()
            resp = c.post(
                "/v1/audio/transcriptions",
                files=[("file", ("t.wav", io.BytesIO(b"x" * (MB + 1)), "audio/wav"))],
            )
        finally:
            app.dependency_overrides.pop(_verify_auth, None)

        assert resp.status_code == 413
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == 413
        assert detail["error"]["type"] == "invalid_request_error"
        mock_t.transcribe.assert_not_called()

    def test_transcriber_failure_cleans_tempfile(self, client, monkeypatch):
        c, mock_t = client
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
        mock_t.reset_mock()
        mock_t.transcribe.side_effect = RuntimeError("boom")
        before = _ds_up_files()

        resp = c.post("/api/transcribe", files=[_audio(size=64)])
        mock_t.transcribe.side_effect = None

        assert resp.status_code == 500
        assert tempfile.gettempdir() not in resp.text
        assert _ds_up_files() == before


class TestUploadValidation:
    def test_zero_byte_file_rejected(self, client):
        c, mock_t = client
        mock_t.reset_mock()

        resp = c.post("/api/transcribe", files=[_audio(size=0)])

        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()
        mock_t.transcribe.assert_not_called()

    def test_overlong_extension_rejected(self, client):
        c, _ = client

        resp = c.post(
            "/api/transcribe",
            files=[
                (
                    "file",
                    (
                        f"t.{'a' * 64}",
                        io.BytesIO(b"x" * 16),
                        "application/octet-stream",
                    ),
                )
            ],
        )

        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_malformed_multipart_is_stable_4xx(self, client):
        c, _ = client

        resp = c.post(
            "/api/transcribe",
            content=b"\x00\x01garbage-not-multipart\xff\xfe",
            headers={"Content-Type": "multipart/form-data; boundary=xbound"},
        )

        assert resp.status_code == 400
        assert "Traceback" not in resp.text

    def test_disk_failure_is_stable_503_without_paths(self, client):
        c, mock_t = client
        mock_t.reset_mock()

        def _failing_ntf(*args, **kwargs):
            raise OSError(28, "No space left on device")

        with patch("routers._uploads.tempfile.NamedTemporaryFile", _failing_ntf):
            resp = c.post("/api/transcribe", files=[_audio(size=64)])

        assert resp.status_code == 503
        body = resp.text
        assert "Traceback" not in body
        assert tempfile.gettempdir() not in body
        mock_t.transcribe.assert_not_called()


class TestRawBodyMiddleware:
    def test_oversize_raw_body_413_before_app(self, client, monkeypatch):
        c, mock_t = client
        monkeypatch.setattr(
            "routers._uploads.BodySizeLimitMiddleware.MULTIPART_OVERHEAD_BYTES", 1024
        )
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
        mock_t.reset_mock()

        resp = c.post(
            "/api/chat", json={"text": "x" * (5 * MB), "messages": []}
        )

        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_v1_oversize_raw_body_openai_error(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(
            "routers._uploads.BodySizeLimitMiddleware.MULTIPART_OVERHEAD_BYTES", 1024
        )
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")

        resp = c.post(
            "/v1/audio/transcriptions",
            content=b"x" * (5 * MB),
            headers={"Content-Type": "multipart/form-data; boundary=xbound"},
        )

        assert resp.status_code == 413
        assert resp.json()["detail"]["error"]["code"] == 413

    def test_get_requests_pass_through(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(
            "routers._uploads.BodySizeLimitMiddleware.MULTIPART_OVERHEAD_BYTES", 1
        )
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")

        resp = c.get("/health")

        assert resp.status_code == 200


class TestMaxUploadEnvParsing:
    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        from routers._uploads import DEFAULT_MAX_UPLOAD_SIZE_MB, max_upload_mb

        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "not-a-number")
        assert max_upload_mb() == DEFAULT_MAX_UPLOAD_SIZE_MB

    def test_unset_uses_default(self):
        from routers._uploads import DEFAULT_MAX_UPLOAD_SIZE_MB, max_upload_mb

        assert max_upload_mb() == DEFAULT_MAX_UPLOAD_SIZE_MB

    def test_zero_is_fail_closed(self, monkeypatch):
        from routers._uploads import max_upload_bytes, max_upload_mb

        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
        assert max_upload_mb() == 0
        assert max_upload_bytes() == 0


class TestSweepStaleTempfiles:
    def test_removes_only_old_app_owned_files(self, tmp_path: Path):
        from routers._uploads import UPLOAD_TEMP_PREFIX, sweep_stale_tempfiles

        old = time.time() - 25 * 3600
        fresh = time.time()

        stale_upload = tmp_path / f"{UPLOAD_TEMP_PREFIX}stale.wav"
        stale_upload.write_bytes(b"old")
        os.utime(stale_upload, (old, old))

        stale_ws = tmp_path / "ds_ws_stale.upload"
        stale_ws.write_bytes(b"old")
        os.utime(stale_ws, (old, old))

        fresh_upload = tmp_path / f"{UPLOAD_TEMP_PREFIX}fresh.wav"
        fresh_upload.write_bytes(b"new")
        os.utime(fresh_upload, (fresh, fresh))

        foreign = tmp_path / "other_old_file.wav"
        foreign.write_bytes(b"mine")
        os.utime(foreign, (old, old))

        stale_dir = tmp_path / f"{UPLOAD_TEMP_PREFIX}dir"
        stale_dir.mkdir()
        os.utime(stale_dir, (old, old))

        removed = sweep_stale_tempfiles(tmp_dir=str(tmp_path))

        assert removed == 2
        assert not stale_upload.exists()
        assert not stale_ws.exists()
        assert fresh_upload.exists()
        assert foreign.exists()
        assert stale_dir.exists()

    def test_custom_age_gate(self, tmp_path: Path):
        from routers._uploads import sweep_stale_tempfiles

        age = time.time() - 2 * 3600
        f = tmp_path / "ds_up_2h.wav"
        f.write_bytes(b"x")
        os.utime(f, (age, age))

        assert sweep_stale_tempfiles(tmp_dir=str(tmp_path)) == 0
        assert sweep_stale_tempfiles(age_seconds=3600, tmp_dir=str(tmp_path)) == 1

    def test_missing_dir_returns_zero(self, tmp_path: Path):
        from routers._uploads import sweep_stale_tempfiles

        assert sweep_stale_tempfiles(tmp_dir=str(tmp_path / "nope")) == 0


class TestChatCaps:
    def _payload(self, **overrides):
        payload = {
            "text": "Спикер 1: текст",
            "messages": [{"role": "user", "content": "вопрос"}],
        }
        payload.update(overrides)
        return payload

    def test_context_over_cap_returns_422(self, client):
        c, _ = client

        resp = c.post("/api/chat", json=self._payload(text="x" * (2_000_001)))

        assert resp.status_code == 422
        assert "Traceback" not in resp.text

    def test_message_content_over_cap_returns_422(self, client):
        c, _ = client

        resp = c.post(
            "/api/chat",
            json=self._payload(messages=[{"role": "user", "content": "y" * 32_769}]),
        )

        assert resp.status_code == 422

    def test_too_many_messages_returns_422(self, client):
        c, _ = client

        resp = c.post(
            "/api/chat",
            json=self._payload(messages=[{"role": "user", "content": "q"}] * 201),
        )

        assert resp.status_code == 422

    def test_model_name_over_cap_returns_422(self, client):
        c, _ = client

        resp = c.post("/api/chat", json=self._payload(model="m" * 101))

        assert resp.status_code == 422

    def test_unlisted_model_returns_stable_400(self, client):
        c, _ = client

        with (
            patch("routers.analysis.llm_client", _mock_llm()),
            patch("routers.analysis.get_available_models", return_value=["glm-5-turbo"]),
        ):
            resp = c.post("/api/chat", json=self._payload(model="gpt-4"))

        assert resp.status_code == 400
        assert "not available" in resp.json()["detail"]

    def test_whitespace_model_returns_400(self, client):
        c, _ = client

        with patch("routers.analysis.get_available_models", return_value=["glm-5-turbo"]):
            resp = c.post("/api/chat", json=self._payload(model="  "))

        assert resp.status_code == 400

    def test_allowlisted_model_passes(self, client):
        c, _ = client

        with (
            patch("routers.analysis.llm_client", _mock_llm()),
            patch("routers.analysis.get_available_models", return_value=["gpt-4"]),
            patch("routers.analysis.chat_with_transcript", return_value={"answer": "ok"}),
        ):
            resp = c.post("/api/chat", json=self._payload(model="gpt-4"))

        assert resp.status_code == 200, resp.text

