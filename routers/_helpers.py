import json
import logging
import secrets
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
from gigaam_transcriber.settings import API_KEY, V1_API_ENABLED
from routers.correlation import get_correlation_id

logger = logging.getLogger("dialogscribe-api")
security = HTTPBearer(auto_error=False)
SUPPORTED_EXTENSIONS = AudioProcessor.AUDIO_FORMATS | AudioProcessor.VIDEO_FORMATS


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Public error payload: stable code + message + correlation id only.

    Raw exception text must never reach the client through this helper;
    the correlation id links the response to server-side log detail.
    """
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "correlation_id": get_correlation_id(),
            }
        },
    )


def _openai_error(message: str, err_type: str, code: int) -> dict[str, dict[str, str | int]]:
    return {"error": {"message": message, "type": err_type, "code": code}}


def _verify_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> None:
    if not V1_API_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=_openai_error(
                "The /v1 API is disabled on this server", "server_error", 503
            ),
        )
    if not API_KEY:
        logger.warning("/v1 request rejected: V1_API_ENABLED=true but API_KEY is not configured")
        raise HTTPException(
            status_code=503,
            detail=_openai_error("The /v1 API is not available", "server_error", 503),
        )
    if not credentials or not secrets.compare_digest(credentials.credentials, API_KEY):
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


_PUBLIC_MESSAGES: dict[type[Exception], str] = {
    AudioTooShortError: "Audio is too short to transcribe",
    EmptyFileError: "Uploaded file is empty",
    EmptyAudioError: "Audio contains no usable signal",
    UnsupportedFormatError: "Unsupported media format",
    FileNotFoundError: "File not found",
    ASRError: "Speech recognition provider error",
    AudioProcessingError: "Audio processing failed",
    DiarizationError: "Speaker diarization failed",
    TranscriberError: "Transcription failed",
}


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
            # OpenAI-compatible /v1 shape: the message is a stable public
            # string (exception text may embed provider bodies or paths);
            # correlation_id ties the response to server-side log detail.
            return HTTPException(
                status_code=code,
                detail=_openai_error(
                    _PUBLIC_MESSAGES[exc_type], type(exc).__name__, code
                )
                | {"correlation_id": get_correlation_id()},
            )
    logger.exception(
        "Unhandled transcription error (correlation_id=%s)", get_correlation_id()
    )
    return HTTPException(
        status_code=500,
        detail=_openai_error(
            "Internal server error", type(exc).__name__, 500
        )
        | {"correlation_id": get_correlation_id()},
    )



