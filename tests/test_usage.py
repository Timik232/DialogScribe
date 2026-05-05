import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from gigaam_transcriber.usage import track_usage, get_usage_stats
from gigaam_transcriber.models import UsageEvent


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_track_usage_creates_event():
    db = _make_db()
    user_id = str(uuid.uuid4())

    event = await track_usage(db, user_id, "transcription_minutes", 5.0)

    assert isinstance(event, UsageEvent)
    assert event.user_id == user_id
    assert event.event_type == "transcription_minutes"
    assert event.value == 5.0
    assert event.metadata_ == {}
    db.add.assert_called_once_with(event)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_track_usage_with_metadata():
    db = _make_db()

    event = await track_usage(db, str(uuid.uuid4()), "llm_call", 1.0, {"type": "chat"})

    assert event.metadata_ == {"type": "chat"}


@pytest.mark.asyncio
async def test_track_usage_default_value():
    db = _make_db()

    event = await track_usage(db, str(uuid.uuid4()), "file_upload")

    assert event.value == 1.0


@pytest.mark.asyncio
async def test_get_usage_stats_returns_rows():
    db = _make_db()
    rows = [
        MagicMock(event_type="transcription_minutes", total=8.5, count=2),
        MagicMock(event_type="llm_call", total=3.0, count=3),
    ]
    mock_result = MagicMock()
    mock_result.all.return_value = rows
    db.execute = AsyncMock(return_value=mock_result)

    stats = await get_usage_stats(db, "user-123")

    assert len(stats) == 2
    assert stats[0]["event_type"] == "transcription_minutes"
    assert stats[0]["total"] == 8.5
    assert stats[0]["count"] == 2
    assert stats[1]["event_type"] == "llm_call"
    assert stats[1]["total"] == 3.0
    assert stats[1]["count"] == 3


@pytest.mark.asyncio
async def test_get_usage_stats_empty():
    db = _make_db()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    stats = await get_usage_stats(db, "user-123")

    assert stats == []


@pytest.mark.asyncio
async def test_get_usage_stats_with_since():
    db = _make_db()
    mock_result = MagicMock()
    mock_result.all.return_value = [MagicMock(event_type="llm_call", total=1.0, count=1)]
    db.execute = AsyncMock(return_value=mock_result)

    since = datetime.utcnow() - timedelta(days=7)
    stats = await get_usage_stats(db, "user-123", since=since)

    assert len(stats) == 1
    assert db.execute.await_count == 1
