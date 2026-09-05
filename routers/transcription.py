import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.data_models import TranscriptionResult
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User, UserSettings
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.rate_limit import user_rate_limit
from gigaam_transcriber.usage import track_usage

from routers._helpers import (
    _handle_transcription_exception,
    _map_diarization,
)
from routers._uploads import spool_upload

router = APIRouter(prefix="/api", tags=["transcription"])


def _segment_to_json(segment) -> dict:
    payload: dict = {
        "text": segment.text,
        "start": segment.start,
        "end": segment.end,
    }
    if segment.speaker is not None:
        payload["speaker"] = segment.speaker
    if segment.confidence is not None:
        payload["confidence"] = segment.confidence
    return payload


def _transcribe_upload(
    file: UploadFile,
    diarization_mode: str | None,
    language: str | None,
    transcriber: GigaAMTranscriber,
    denoise: str | None = None,
    *,
    provider_preference: str | None = None,
) -> dict:
    try:
        with spool_upload(file) as tmp_path:
            result: TranscriptionResult = transcriber.transcribe(
                input_path=tmp_path,
                diarization=_map_diarization(diarization_mode),
                language=language or "ru",
                denoise=denoise or "none",
                provider_preference=provider_preference,
            )
        return {
            "segments": [_segment_to_json(seg) for seg in result.segments],
            "duration": result.duration,
            "text": result.text,
            "language": result.language,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_transcription_exception(e)


@router.post("/transcribe", dependencies=[Depends(user_rate_limit("upload"))])
async def transcribe(
    request: Request,
    file: Annotated[UploadFile, File()],
    diarization_mode: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    denoise: Annotated[str | None, Form()] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await check_limit(db, user.id, "transcription_minutes")
    stmt = select(UserSettings.asr_provider).where(UserSettings.user_id == user.id)
    db_result = await db.execute(stmt)
    row = db_result.first()
    provider_pref = row[0] if row else None
    transcriber = request.app.state.transcriber
    result = await asyncio.to_thread(
        _transcribe_upload, file, diarization_mode, language, transcriber,
        denoise=denoise, provider_preference=provider_pref,
    )
    duration_minutes = result.get("duration", 0) / 60
    await track_usage(db, user.id, "transcription_minutes", duration_minutes)
    await track_usage(db, user.id, "file_upload", 1.0)
    return result


@router.post("/transcribe/microphone", dependencies=[Depends(user_rate_limit("upload"))])
async def transcribe_microphone(
    request: Request,
    file: Annotated[UploadFile, File()],
    diarization_mode: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    denoise: Annotated[str | None, Form()] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await check_limit(db, user.id, "transcription_minutes")
    stmt = select(UserSettings.asr_provider).where(UserSettings.user_id == user.id)
    db_result = await db.execute(stmt)
    row = db_result.first()
    provider_pref = row[0] if row else None
    transcriber = request.app.state.transcriber
    result = await asyncio.to_thread(
        _transcribe_upload, file, diarization_mode, language, transcriber,
        denoise=denoise, provider_preference=provider_pref,
    )
    duration_minutes = result.get("duration", 0) / 60
    await track_usage(db, user.id, "transcription_minutes", duration_minutes)
    return result
