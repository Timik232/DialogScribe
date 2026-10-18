"""Background transcription jobs API (docs/design/background-jobs.md).

Covers the mobile-disconnect contract:
- POST /api/jobs returns 202 + job id immediately, file lands in the job store
- the job runs to completion even though no client ever polls
- the result is auto-saved into saved_transcriptions
- GET /api/jobs/{id} exposes status/progress/result for polling
- DELETE cancels a queued/processing job
- users only see their own jobs; invalid params are rejected
"""

import asyncio
import io
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import setup_auth_override


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    store = tmp_path / "jobs"
    monkeypatch.setenv("JOB_STORE_DIR", str(store))
    import gigaam_transcriber.jobs as jobs_module
    jobs_module.JOB_STORE_DIR = store
    return store


def _mock_transcriber(text="job result text", duration=3.0):
    t = MagicMock()
    seg = SimpleNamespace(text=text, start=0.0, end=duration, speaker=None, confidence=None)
    t.transcribe.return_value = SimpleNamespace(
        segments=[seg], duration=duration, text=text, language="ru"
    )
    return t


def _audio_file(filename="voice.wav", content=b"fake audio"):
    return ("file", (filename, io.BytesIO(content), "audio/wav"))


@pytest.fixture()
def client(job_env):
    mock_transcriber = _mock_transcriber()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.jobs.check_limit"),
        patch("gigaam_transcriber.usage.track_usage"),
    ):
        from api import app
        from gigaam_transcriber.database import Base, engine

        async def _create_tables():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create_tables())

        setup_auth_override(app)
        with TestClient(app) as c:
            yield c, mock_transcriber, app
        app.dependency_overrides.clear()


def _wait_terminal(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last and last.get("status") in ("completed", "failed", "cancelled"):
            return last
        time.sleep(0.02)
    assert last, "job status never became queryable"
    return last


class TestJobsApi:
    def test_submit_and_complete(self, client):
        c, mock_t, app = client

        resp = c.post("/api/jobs", files=[_audio_file("voice.wav")])
        assert resp.status_code == 202, resp.text
        job = resp.json()
        assert job["status"] in ("queued", "processing")
        assert job["id"]

        final = _wait_terminal(c, job["id"])
        assert final["status"] == "completed", final
        assert final["result"]["text"] == "job result text"
        assert final["saved_transcription_id"]

        # auto-saved into history
        saved = c.get("/api/saved-transcriptions").json()
        assert any(item["id"] == final["saved_transcription_id"] for item in saved)


    def test_input_file_lands_in_store_and_is_removed(self, client, job_env):
        c, mock_t, app = client
        resp = c.post("/api/jobs", files=[_audio_file("meeting.wav")])
        job = resp.json()
        files = list(job_env.glob("*"))
        assert any(f.name.startswith(job["id"]) for f in files)
        assert job_env is not None
        final = _wait_terminal(c, job["id"])
        assert final["status"] == "completed"
        assert not any(f.name.startswith(job["id"]) for f in job_env.glob("*"))

    def test_list_and_poll_shape(self, client):
        c, _, _ = client
        resp = c.post("/api/jobs", files=[_audio_file("a.wav")])
        job_id = resp.json()["id"]
        _wait_terminal(c, job_id)

        listing = c.get("/api/jobs").json()
        assert any(j["id"] == job_id for j in listing)
        one = c.get(f"/api/jobs/{job_id}").json()
        for key in ("id", "status", "stage", "progress", "created_at"):
            assert key in one

    def test_cancel_queued_job(self, client):
        c, _, _ = client
        release = threading.Event()

        t = _mock_transcriber()
        # make transcribe block so the job stays processing
        def slow_transcribe(**kwargs):
            release.wait(5)
            return SimpleNamespace(
                segments=[SimpleNamespace(text="x", start=0.0, end=1.0, speaker=None, confidence=None)],
                duration=1.0,
                text="x",
                language="ru",
            )

        t.transcribe = slow_transcribe
        # swap the app-level transcriber used by launch_job
        from api import app as live_app
        live_app.state.transcriber = t

        resp = c.post("/api/jobs", files=[_audio_file("cancel-me.wav")])
        job_id = resp.json()["id"]
        # wait until processing
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            st = c.get(f"/api/jobs/{job_id}").json()
            if st["status"] == "processing":
                break
            time.sleep(0.02)
        assert st["status"] == "processing"

        cancel = c.delete(f"/api/jobs/{job_id}")
        assert cancel.status_code == 200
        release.set()
        final = _wait_terminal(c, job_id)
        assert final["status"] == "cancelled"

    def test_validation(self, client):
        c, _, _ = client
        # bad diarization
        resp = c.post(
            "/api/jobs",
            files=[_audio_file()],
            data={"diarization_mode": "nonsense"},
        )
        assert resp.status_code == 400
        # bad extension
        resp = c.post("/api/jobs", files=[_audio_file("virus.exe")])
        assert resp.status_code == 400
        # traversal filename
        resp = c.post("/api/jobs", files=[_audio_file("../evil.wav")])
        assert resp.status_code == 400

    def test_foreign_job_404(self, client):
        c, _, _ = client
        resp = c.get("/api/jobs/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_failure_marks_job_failed(self, client):
        c, mock_t, _ = client
        mock_t.transcribe.side_effect = RuntimeError("asr down")

        resp = c.post("/api/jobs", files=[_audio_file("bad.wav")])
        job_id = resp.json()["id"]
        final = _wait_terminal(c, job_id)
        assert final["status"] == "failed"
        assert "asr down" in final["error"]
        # input removed on failure too
        assert not any(f.name.startswith(job_id) for f in job_env_dir(c).glob("*"))


def job_env_dir(client):
    import gigaam_transcriber.jobs as jobs_module
    return jobs_module.JOB_STORE_DIR
