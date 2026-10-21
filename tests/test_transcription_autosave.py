"""Quick-win #3: /api/transcribe auto-persists its result server-side.

Even when the HTTP response never reaches the client (screen off, app
killed), the transcription must be waiting in the user's history.
"""

import asyncio
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import routers.transcription as transcription_router
from tests.conftest import make_mock_user


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with (
        patch("api.GigaAMTranscriber", return_value=mock_transcriber),
        patch("routers.transcription.check_limit"),
        patch("routers.transcription.track_usage"),
    ):
        from api import app
        from gigaam_transcriber.database import Base, engine

        async def _create_tables():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create_tables())

        # Key the override by the function object the ROUTER module holds.
        # Earlier suites (test_settings_validation) importlib.reload() the
        # auth module, which desynchronizes ``from ... import get_current_user``
        # in conftest vs the router — the override silently stops matching
        # (401s) in full-run order. Referencing via the router module keeps
        # the exact object the app's routes were built with.
        auth_key = transcription_router.get_current_user
        app.dependency_overrides[auth_key] = lambda: make_mock_user()
        with TestClient(app) as c:
            yield c, mock_transcriber
        app.dependency_overrides.pop(auth_key, None)


def _mock_result(text="Hello world", duration=1.5):
    seg = MagicMock()
    seg.text = text
    seg.start = 0.0
    seg.end = duration
    seg.speaker = None
    seg.confidence = None
    result = MagicMock()
    result.segments = [seg]
    result.duration = duration
    result.text = text
    result.language = "en"
    return result


class TestAutoPersistence:
    def test_transcribe_schedules_persist(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result("auto-persist me", 2.0)

        scheduled = []

        async def fake_schedule(factory, user_id, result, *, origin, filename=None):
            scheduled.append((user_id, result, origin, filename))

        with patch.object(transcription_router, "schedule_persist", fake_schedule):
            resp = c.post(
                "/api/transcribe",
                files=[("file", ("voice.wav", io.BytesIO(b"fake audio"), "audio/wav"))],
            )

        assert resp.status_code == 200
        assert scheduled, "persistence was not scheduled after successful transcription"
        user_id, result, origin, filename = scheduled[0]
        assert result["text"] == "auto-persist me"
        assert origin == "upload"
        assert filename == "voice.wav"

    def test_microphone_schedules_persist(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result("mic text", 1.0)

        scheduled = []

        async def fake_schedule(factory, user_id, result, *, origin, filename=None):
            scheduled.append((user_id, result, origin, filename))

        with patch.object(transcription_router, "schedule_persist", fake_schedule):
            resp = c.post(
                "/api/transcribe/microphone",
                files=[("file", ("mic.wav", io.BytesIO(b"fake audio"), "audio/wav"))],
            )

        assert resp.status_code == 200
        assert scheduled
        assert scheduled[0][2] == "microphone"

    def test_persist_failure_does_not_break_response(self, client):
        c, mock_t = client
        mock_t.transcribe.return_value = _mock_result("still delivered", 1.0)

        async def exploding_schedule(*a, **k):
            raise RuntimeError("db down")

        with patch.object(transcription_router, "schedule_persist", exploding_schedule):
            resp = c.post(
                "/api/transcribe",
                files=[("file", ("x.wav", io.BytesIO(b"fake audio"), "audio/wav"))],
            )

        assert resp.status_code == 200
        assert resp.json()["text"] == "still delivered"

    def test_schedule_persist_real_insert(self, client):
        """schedule_persist against the real sqlite DB actually writes a row."""
        from sqlalchemy import select

        from gigaam_transcriber.database import async_session_factory
        from gigaam_transcriber.models import SavedTranscription
        from routers._persistence import schedule_persist

        payload = {
            "text": "durable text",
            "segments": [{"text": "durable text", "start": 0.0, "end": 1.0}],
            "duration": 1.0,
            "language": "ru",
        }

        async def _run():
            await schedule_persist(
                async_session_factory, "user-auto-persist", payload,
                origin="upload", filename="voice.wav",
            )
            async with async_session_factory() as db:
                rows = (
                    await db.execute(
                        select(SavedTranscription).where(
                            SavedTranscription.user_id == "user-auto-persist"
                        )
                    )
                ).scalars().all()
                return rows

        rows = asyncio.run(_run())
        assert len(rows) == 1
        assert rows[0].full_text == "durable text"
        assert rows[0].title.startswith("voice —")
        assert rows[0].duration == 1.0

        # cleanup
        async def _cleanup():
            async with async_session_factory() as db:
                for row in rows:
                    await db.delete(row)
                await db.commit()

        asyncio.run(_cleanup())
