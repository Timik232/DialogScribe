import json
import logging
import os
from typing import Annotated

from fastapi import Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gigaam_transcriber.data_models import OutputFormat, TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.audio_processor import AudioProcessor
from gigaam_transcriber.exceptions import (
    ASRError,
    AudioProcessingError,
    AudioTooShortError,
    DiarizationError,
    EmptyAudioError,
    EmptyFileError,
    FileNotFoundError,
    TranscriberError,
    UnsupportedFormatError,
)

API_KEY = os.getenv("API_KEY", "")

logger = logging.getLogger("dialogscribe-api")
security = HTTPBearer(auto_error=False)
SUPPORTED_EXTENSIONS = AudioProcessor.AUDIO_FORMATS | AudioProcessor.VIDEO_FORMATS


def _openai_error(message: str, err_type: str, code: int) -> dict[str, dict[str, str | int]]:
    return {"error": {"message": message, "type": err_type, "code": code}}


def _verify_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> None:
    if not API_KEY:
        return
    if not credentials or credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=401,
            detail=_openai_error("Invalid API key", "authentication_error", 401),
        )

def _map_diarization(mode: str | None) -> str:
    """Map frontend diarization values to backend DiarizationMode."""
    mapping = {
        "none": "none",
        "simple": "hybrid",
        "advanced": "pyannote",
        "hybrid": "hybrid",
        "pyannote": "pyannote",
    }
    return mapping.get(mode or "none", "none")


def _map_format(fmt: str) -> OutputFormat:
    mapping: dict[str, OutputFormat] = {
        "json": "txt",
        "text": "txt",
        "verbose_json": "json",
        "srt": "srt",
        "vtt": "vtt",
    }
    if fmt in mapping:
        return mapping[fmt]
    return "txt"


def _segment_to_dict(segment: TranscriptionSegment) -> dict[str, object]:
    payload: dict[str, object] = {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
    }
    if segment.speaker is not None:
        payload["speaker"] = segment.speaker
    if segment.confidence is not None:
        payload["confidence"] = segment.confidence
    if segment.words:
        payload["words"] = [w.to_dict() for w in segment.words]
    return payload


def _result_response(result: TranscriptionResult, response_format: str) -> Response:
    if response_format == "json":
        return Response(
            content=json.dumps({"text": result.text}, ensure_ascii=False),
            media_type="application/json",
        )
    if response_format == "text":
        return Response(content=result.to_txt(), media_type="text/plain; charset=utf-8")
    if response_format == "verbose_json":
        payload = {
            "task": "transcribe",
            "language": result.language,
            "duration": result.duration,
            "text": result.text,
            "segments": [_segment_to_dict(seg) for seg in result.segments],
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False), media_type="application/json"
        )
    if response_format == "srt":
        return Response(content=result.to_srt(), media_type="text/plain; charset=utf-8")
    if response_format == "vtt":
        return Response(content=result.to_vtt(), media_type="text/plain; charset=utf-8")
    raise HTTPException(
        status_code=400,
        detail=_openai_error(
            "Invalid response_format. Use one of: json, text, verbose_json, srt, vtt",
            "invalid_request_error",
            400,
        ),
    )


def _handle_transcription_exception(exc: Exception) -> HTTPException:
    status_map: list[tuple[type[Exception], int]] = [
        (AudioTooShortError, 400),
        (EmptyFileError, 400),
        (EmptyAudioError, 400),
        (UnsupportedFormatError, 400),
        (FileNotFoundError, 404),
        (ASRError, 502),
        (AudioProcessingError, 500),
        (DiarizationError, 500),
        (TranscriberError, 500),
    ]
    for exc_type, code in status_map:
        if isinstance(exc, exc_type):
            return HTTPException(
                status_code=code, detail=_openai_error(str(exc), type(exc).__name__, code)
            )
    logger.exception("Unhandled error during transcription")
    return HTTPException(status_code=500, detail=_openai_error(str(exc), type(exc).__name__, 500))



