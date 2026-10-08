import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.exporters import export_docx_transcription, export_pdf_transcription, export_docx_insights
from gigaam_transcriber.models import User

from routers._helpers import logger

router = APIRouter(prefix="/api", tags=["exports"])

SUPPORTED_FORMATS = ["json", "srt", "vtt", "txt", "docx", "pdf"]

CONTENT_TYPES: Dict[str, str] = {
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "srt": "text/plain; charset=utf-8",
    "vtt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

_EXPORT_FAILED_DETAIL = "Export generation failed. Please try again."


class ExportRequest(BaseModel):
    data: Dict[str, Any]
    format: str
    filename: str
    speaker_names: Dict[str, str] | None = None


class ExportInsightsRequest(BaseModel):
    action_items: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    suggested_steps: List[Dict[str, Any]] = []
    format: str = "txt"


def _result_from_dict(obj: Dict[str, Any], speaker_names: Dict[str, str] | None = None) -> TranscriptionResult:
    segments = []
    for s in obj.get("segments", []):
        words = None
        if s.get("words"):
            from gigaam_transcriber.data_models import WordSegment
            words = [WordSegment(**w) if isinstance(w, dict) else w for w in s["words"]]
        speaker = s.get("speaker")
        if speaker_names and speaker and speaker in speaker_names:
            speaker = speaker_names[speaker]
        segments.append(
            TranscriptionSegment(
                text=s["text"],
                start=s["start"],
                end=s["end"],
                speaker=speaker,
                confidence=s.get("confidence"),
                words=words,
            )
        )

    text = obj["text"]
    if speaker_names:
        for original, replacement in speaker_names.items():
            text = text.replace(original, replacement)

    return TranscriptionResult(
        text=text,
        segments=segments,
        duration=obj.get("duration", 0.0),
        language=obj.get("language", "unknown"),
        model_name=obj.get("model_name", ""),
        processing_time=obj.get("processing_time", 0.0),
        metadata=obj.get("metadata", {}),
    )


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("Failed to remove export tempfile: %s", exc)


class _ExportFileResponse(FileResponse):
    """FileResponse that removes the export tempfile on every exit path.

    Starlette runs ``background`` only after a fully streamed response; when
    the client aborts mid-download the background task never runs, so this
    subclass unlinks in a ``finally`` guard too. Both paths delete strictly
    after FileResponse has finished reading the file.
    """

    def __init__(self, *args: Any, cleanup_path: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cleanup_path = cleanup_path

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            _cleanup(self._cleanup_path)


@router.post("/export")
async def export_transcription(
    body: ExportRequest,
    background_tasks: BackgroundTasks,
    _user: User = Depends(get_current_user),
) -> FileResponse:
    fmt = body.format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{fmt}'. Supported: {', '.join(SUPPORTED_FORMATS)}",
        )

    try:
        result = _result_from_dict(body.data, body.speaker_names)
    except Exception:
        logger.exception("Invalid transcription payload for export")
        raise HTTPException(status_code=422, detail="Invalid transcription payload") from None

    ext_map = {"json": ".json", "srt": ".srt", "vtt": ".vtt", "txt": ".txt", "docx": ".docx", "pdf": ".pdf"}
    ext = ext_map[fmt]

    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        if fmt in ("docx", "pdf"):
            if fmt == "docx":
                await asyncio.to_thread(export_docx_transcription, result, tmp)
            else:
                await asyncio.to_thread(export_pdf_transcription, result, tmp)
        else:
            content = result.to_json() if fmt == "json" else result.to_txt() if fmt == "txt" else result.to_srt() if fmt == "srt" else result.to_vtt()
            Path(tmp).write_text(content, encoding="utf-8")

        if not Path(tmp).exists():
            raise OSError("exporter produced no output file")
    except asyncio.CancelledError:
        if tmp:
            _cleanup(tmp)
        raise
    except Exception:
        logger.exception("Export generation failed (format=%s)", fmt)
        if tmp:
            _cleanup(tmp)
        raise HTTPException(status_code=502, detail=_EXPORT_FAILED_DETAIL) from None

    filename = f"{body.filename}{ext}"
    background_tasks.add_task(_cleanup, tmp)

    return _ExportFileResponse(
        path=tmp,
        filename=filename,
        media_type=CONTENT_TYPES[fmt],
        background=background_tasks,
        cleanup_path=tmp,
    )


@router.post("/export-insights")
async def export_insights(
    body: ExportInsightsRequest,
    background_tasks: BackgroundTasks,
    _user: User = Depends(get_current_user),
) -> FileResponse:
    fmt = body.format.lower()
    if fmt not in ("txt", "docx"):
        raise HTTPException(status_code=400, detail="Unsupported format '{fmt}'. Supported: txt, docx")

    from gigaam_transcriber.insights import export_insights_txt

    ext = ".txt" if fmt == "txt" else ".docx"
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        if fmt == "docx":
            await asyncio.to_thread(
                export_docx_insights,
                body.action_items,
                body.decisions,
                body.suggested_steps,
                tmp,
            )
        else:
            content = await asyncio.to_thread(
                export_insights_txt,
                body.action_items,
                body.decisions,
                body.suggested_steps,
            )
            Path(tmp).write_text(content, encoding="utf-8")

        if not Path(tmp).exists():
            raise OSError("exporter produced no output file")
    except asyncio.CancelledError:
        if tmp:
            _cleanup(tmp)
        raise
    except Exception:
        logger.exception("Insights export generation failed (format=%s)", fmt)
        if tmp:
            _cleanup(tmp)
        raise HTTPException(status_code=502, detail=_EXPORT_FAILED_DETAIL) from None

    background_tasks.add_task(_cleanup, tmp)
    return _ExportFileResponse(
        path=tmp,
        filename=f"insights{ext}",
        media_type=CONTENT_TYPES.get(fmt, "text/plain"),
        background=background_tasks,
        cleanup_path=tmp,
    )
