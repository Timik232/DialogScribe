import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from gigaam_transcriber.limits import check_limit


def _make_db(limit_row=None, usage_value=0.0):
    db = AsyncMock()

    limit_result = MagicMock()
    limit_result.scalar_one_or_none.return_value = limit_row

    usage_result = MagicMock()
    usage_result.scalar.return_value = usage_value

    call_count = 0

    async def mock_execute(query):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return limit_result
        return usage_result

    db.execute = mock_execute
    return db


def _make_limit(limit_type="transcription_minutes", max_value=100.0, period="monthly", enabled=True):
    limit = MagicMock()
    limit.limit_type = limit_type
    limit.max_value = max_value
    limit.period = period
    limit.enabled = enabled
    return limit


@pytest.mark.asyncio
async def test_no_limit_passes():
    db = _make_db(limit_row=None)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_disabled_limit_passes():
    limit = _make_limit(enabled=False)
    db = _make_db(limit_row=limit)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_within_limit_passes():
    limit = _make_limit(max_value=100.0)
    db = _make_db(limit_row=limit, usage_value=50.0)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_at_limit_raises_429():
    limit = _make_limit(max_value=100.0)
    db = _make_db(limit_row=limit, usage_value=100.0)

    with pytest.raises(HTTPException) as exc_info:
        await check_limit(db, "user-123", "transcription_minutes")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_over_limit_raises_429():
    limit = _make_limit(limit_type="llm_call", max_value=10.0)
    db = _make_db(limit_row=limit, usage_value=13.0)

    with pytest.raises(HTTPException) as exc_info:
        await check_limit(db, "user-123", "llm_call")
    assert exc_info.value.status_code == 429
    assert "llm_call" in exc_info.value.detail


@pytest.mark.asyncio
async def test_error_detail_contains_limit_info():
    limit = _make_limit(max_value=50.0)
    db = _make_db(limit_row=limit, usage_value=75.0)

    with pytest.raises(HTTPException) as exc_info:
        await check_limit(db, "user-123", "transcription_minutes")
    detail = exc_info.value.detail
    assert "50" in detail
    assert "75" in detail


@pytest.mark.asyncio
async def test_daily_period_check():
    limit = _make_limit(period="daily", max_value=10.0)
    db = _make_db(limit_row=limit, usage_value=5.0)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_monthly_period_check():
    limit = _make_limit(period="monthly", max_value=100.0)
    db = _make_db(limit_row=limit, usage_value=99.0)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_zero_usage_within_limit():
    limit = _make_limit(max_value=10.0)
    db = _make_db(limit_row=limit, usage_value=0.0)
    await check_limit(db, "user-123", "transcription_minutes")


@pytest.mark.asyncio
async def test_different_limit_types():
    limit = _make_limit(limit_type="llm_call", max_value=5.0)
    db = _make_db(limit_row=limit, usage_value=3.0)
    await check_limit(db, "user-123", "llm_call")
