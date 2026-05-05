import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.data_models import TranscriptionResult
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.usage import track_usage

from routers._helpers import (
    SUPPORTED_EXTENSIONS,
    _handle_transcription_exception,
    _map_diarization,
    logger,
)

router = APIRouter(prefix="/api", tags=["transcription"])

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "1024"))


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
) -> dict:
    filename = file.filename or ""
    file_ext = Path(filename).suffix.lower()

    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: '{file_ext or 'unknown'}'",
        )

    max_size_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
            tmp_path = tmp_file.name
            size = 0
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum allowed is {MAX_UPLOAD_SIZE_MB}MB",
                    )
                tmp_file.write(chunk)

        result: TranscriptionResult = transcriber.transcribe(
            input_path=tmp_path,
            diarization=_map_diarization(diarization_mode),
            language=language or "ru",
            denoise=denoise or "none",
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
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            logger.warning("Failed to remove temp file: %s", tmp_path)


@router.post("/transcribe")
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
    transcriber = request.app.state.transcriber
    result = _transcribe_upload(file, diarization_mode, language, transcriber, denoise=denoise)
    duration_minutes = result.get("duration", 0) / 60
    await track_usage(db, user.id, "transcription_minutes", duration_minutes)
    await track_usage(db, user.id, "file_upload", 1.0)
    return result


@router.post("/transcribe/microphone")
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
    transcriber = request.app.state.transcriber
    result = _transcribe_upload(file, diarization_mode, language, transcriber, denoise=denoise)
    duration_minutes = result.get("duration", 0) / 60
    await track_usage(db, user.id, "transcription_minutes", duration_minutes)
    return result
