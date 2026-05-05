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
    except OSError:
        pass


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

    result = _result_from_dict(body.data, body.speaker_names)
    ext_map = {"json": ".json", "srt": ".srt", "vtt": ".vtt", "txt": ".txt", "docx": ".docx", "pdf": ".pdf"}
    ext = ext_map[fmt]

    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)

    if fmt in ("docx", "pdf"):
        path = Path(tmp)
        path.unlink(missing_ok=True)
        actual = path.with_suffix(ext)
        if fmt == "docx":
            export_docx_transcription(result, str(actual))
        else:
            export_pdf_transcription(result, str(actual))
        tmp = str(actual)

    try:
        if fmt in ("json", "srt", "vtt", "txt"):
            content = result.to_json() if fmt == "json" else result.to_txt() if fmt == "txt" else result.to_srt() if fmt == "srt" else result.to_vtt()
            Path(tmp).write_text(content, encoding="utf-8")
    except Exception:
        _cleanup(tmp)
        raise

    filename = f"{body.filename}{ext}"
    background_tasks.add_task(_cleanup, tmp)

    return FileResponse(
        path=tmp,
        filename=filename,
        media_type=CONTENT_TYPES[fmt],
        background=background_tasks,
    )


@router.post("/export-insights")
async def export_insights(
    body: ExportInsightsRequest,
    background_tasks: BackgroundTasks,
    _user: User = Depends(get_current_user),
) -> FileResponse:
    fmt = body.format.lower()
    if fmt not in ("txt", "docx"):
        raise HTTPException(status_code=400, detail=f"Unsupported format '{fmt}'. Supported: txt, docx")

    from gigaam_transcriber.insights import export_insights_txt

    ext = ".txt" if fmt == "txt" else ".docx"
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)

    if fmt == "docx":
        Path(tmp).unlink(missing_ok=True)
        tmp_docx = tmp if tmp.endswith(".docx") else tmp + ".docx"
        export_docx_insights(body.action_items, body.decisions, body.suggested_steps, tmp_docx)
        tmp = tmp_docx
    else:
        content = export_insights_txt(body.action_items, body.decisions, body.suggested_steps)
        Path(tmp).write_text(content, encoding="utf-8")

    background_tasks.add_task(_cleanup, tmp)
    return FileResponse(
        path=tmp,
        filename=f"insights{ext}",
        media_type=CONTENT_TYPES.get(fmt, "text/plain"),
        background=background_tasks,
    )
