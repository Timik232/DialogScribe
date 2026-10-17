"""Server-side persistence helpers for transcription results.

Quick-win #3 (docs/design/background-jobs.md): every finished transcription
is stored as a ``SavedTranscription`` row on the server, so a lost HTTP
response (mobile screen off, app killed, network drop) no longer loses the
result — it is waiting in the user's history when they come back.

The background-jobs API (routers/jobs.py) reuses the same helpers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.models import SavedTranscription

logger = logging.getLogger("dialogscribe-api")

_ORIGIN_LABELS = {
    "upload": "Транскрипция",
    "microphone": "Запись с микрофона",
    "autoflow": "Autoflow",
    "job": "Обработка",
}


def _default_title(origin: str, duration: float, filename: str | None) -> str:
    now = datetime.utcnow()
    label = _ORIGIN_LABELS.get(origin, _ORIGIN_LABELS["upload"])
    total = int(duration or 0)
    mins, secs = total // 60, total % 60
    base = now.strftime("%d.%m.%Y %H:%M")
    if filename:
        stem = str(filename).rsplit(".", 1)[0][:80]
        return f"{stem} — {base}"
    return f"{label} {base} — {mins}m {secs}s"


def _normalize_segments(value: Any) -> list[Any]:
    """Normalize a segments payload to the canonical list shape.

    Mirrors ``saved_transcriptions._normalize_segments`` for the plain-dict
    shapes the transcription pipeline produces.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        if not value:
            return []
        inner = value.get("segments") if "segments" in value else None
        if isinstance(inner, list):
            return inner
        if all(str(k).isdigit() for k in value):
            return [value[k] for k in sorted(value, key=int)]
        return [dict(value)]
    return list(value)


async def persist_transcription_result(
    db: AsyncSession,
    user_id: str,
    result: Mapping[str, Any],
    *,
    origin: str,
    filename: str | None = None,
) -> SavedTranscription | None:
    """Insert a SavedTranscription row for a finished transcription.

    ``result`` is the API response dict (segments/duration/text/language).
    Returns the persisted object, or None when there is nothing worth
    saving (empty text). The caller commits.
    """
    text = str(result.get("text") or "").strip()
    if not text:
        return None

    obj = SavedTranscription(
        user_id=user_id,
        title=_default_title(origin, float(result.get("duration") or 0.0), filename),
        full_text=text,
        segments_json=_normalize_segments(result.get("segments")),
        speaker_names=dict(result.get("speaker_names") or {}),
        duration=float(result.get("duration") or 0.0),
        language=str(result.get("language") or "ru"),
    )
    db.add(obj)
    await db.flush()
    return obj


async def schedule_persist(
    session_factory: Any,
    user_id: str,
    result: Mapping[str, Any],
    *,
    origin: str,
    filename: str | None = None,
) -> None:
    """Fire-and-forget persistence on a FRESH session.

    Never blocks or breaks the response path: any failure is logged and
    swallowed. A fresh session is opened deliberately — the request's own
    session may be closed as soon as the response is sent, and the insert
    must not race the request teardown.
    """
    try:
        async with session_factory() as db:
            obj = await persist_transcription_result(
                db, user_id, result, origin=origin, filename=filename
            )
            if obj is not None:
                await db.commit()
                logger.info("Transcription auto-saved (origin=%s, id=%s)", origin, obj.id)
    except Exception:
        logger.exception("Auto-save of transcription result failed")
