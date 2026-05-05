import uuid
from datetime import datetime

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.insights import extract_action_items, generate_suggested_steps
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.mindmap import generate_mindmap_markdown
from gigaam_transcriber.models import SavedTranscription, User
from gigaam_transcriber.summarizer import LLMClient, generate_summary
from gigaam_transcriber.usage import track_usage

from routers._helpers import logger

router = APIRouter(prefix="/api", tags=["saved-transcriptions"])

llm_client = LLMClient()


def _ensure_llm() -> None:
    if not llm_client.config.api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY not configured")


# ── Pydantic schemas ────────────────────────────────────────────────

class SaveRequest(BaseModel):
    title: str | None = None
    full_text: str
    segments: Sequence[Any] | None = None
    speaker_names: Mapping[str, Any] | None = None
    duration: float
    language: str = "ru"


class UpdateRequest(BaseModel):
    title: str | None = None
    full_text: str | None = None


class TranscriptionResponse(BaseModel):
    id: str
    title: str
    full_text: str
    analysis_text: str | None = None
    segments_json: list | None
    speaker_names: Mapping[str, Any] | None
    duration: float
    language: str
    share_id: str | None
    created_at: datetime
    updated_at: datetime


class TranscriptionListItem(BaseModel):
    id: str
    title: str
    duration: float
    language: str
    created_at: datetime
    updated_at: datetime


class ShareResponse(BaseModel):
    share_id: str
    share_url: str


class PublicShareResponse(BaseModel):
    title: str
    full_text: str
    analysis_text: str | None = None
    segments_json: list | None
    speaker_names: Mapping[str, Any] | None
    duration: float
    language: str
    created_at: datetime


# ── Helpers ──────────────────────────────────────────────────────────

def _to_dict(t: SavedTranscription) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "full_text": t.full_text,
        "analysis_text": t.analysis_text,
        "segments_json": t.segments_json,
        "speaker_names": t.speaker_names,
        "duration": t.duration,
        "language": t.language,
        "share_id": t.share_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _to_list_item(t: SavedTranscription) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "duration": t.duration,
        "language": t.language,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _generate_default_title(duration: float) -> str:
    now = datetime.utcnow()
    mins = int(duration) // 60
    secs = int(duration) % 60
    return f"{now.strftime('%d.%m.%Y %H:%M')} — {mins}m {secs}s"


# ── CRUD endpoints ───────────────────────────────────────────────────

@router.post("/saved-transcriptions", status_code=201)
async def create_transcription(
    body: SaveRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.full_text or not body.full_text.strip():
        raise HTTPException(status_code=422, detail="full_text must not be empty")

    title = body.title or _generate_default_title(body.duration)

    obj = SavedTranscription(
        user_id=_user.id,
        title=title,
        full_text=body.full_text,
        segments_json=body.segments or {},
        speaker_names=body.speaker_names or {},
        duration=body.duration,
        language=body.language,
    )
    db.add(obj)
    await db.flush()
    await db.commit()
    await db.refresh(obj)
    return _to_dict(obj)


@router.get("/saved-transcriptions")
async def list_transcriptions(
    q: str | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SavedTranscription).where(
        SavedTranscription.user_id == _user.id,
    )

    if q:
        stmt = stmt.where(
            sa_func.lower(SavedTranscription.title).like(f"%{q.lower()}%")
            | sa_func.lower(SavedTranscription.full_text).like(f"%{q.lower()}%")
        )

    stmt = stmt.order_by(SavedTranscription.created_at.desc())
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [_to_list_item(t) for t in rows]


@router.get("/saved-transcriptions/{transcription_id}")
async def get_transcription(
    transcription_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")
    return _to_dict(obj)


@router.put("/saved-transcriptions/{transcription_id}")
async def update_transcription(
    transcription_id: str,
    body: UpdateRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")

    if body.title is not None:
        obj.title = body.title
    if body.full_text is not None:
        obj.full_text = body.full_text
    obj.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(obj)
    return _to_dict(obj)


@router.delete("/saved-transcriptions/{transcription_id}", status_code=204)
async def delete_transcription(
    transcription_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")

    await db.delete(obj)
    await db.commit()
    return None


# ── Analyze endpoint ─────────────────────────────────────────────────

@router.post("/saved-transcriptions/{transcription_id}/analyze")
async def analyze_transcription(
    transcription_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ensure_llm()
    await check_limit(db, _user.id, "llm_call")

    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")

    if not obj.full_text or not obj.full_text.strip():
        raise HTTPException(status_code=400, detail="Transcription has no text to analyze")

    try:
        summary_md = await generate_summary(obj.full_text, "general", llm_client)
        mindmap_md = generate_mindmap_markdown(obj.full_text, llm_client)
        insights = extract_action_items(obj.full_text, llm_client)
        steps = generate_suggested_steps(obj.full_text, llm_client)

        insights_parts = []
        if insights.get("action_items"):
            insights_parts.append(
                "## Задачи\n"
                + "\n".join(f"- {item}" for item in insights["action_items"])
            )
        if insights.get("decisions"):
            insights_parts.append(
                "## Решения\n"
                + "\n".join(f"- {item}" for item in insights["decisions"])
            )
        insights_text = "\n\n".join(insights_parts) if insights_parts else "Нет данных"

        if steps.get("suggested_steps"):
            steps_text = "\n".join(
                f"{i + 1}. {step}" for i, step in enumerate(steps["suggested_steps"])
            )
        else:
            steps_text = "Нет данных"

        analysis_text = (
            f"# Сводка\n{summary_md}\n\n"
            f"# Интеллект-карта\n{mindmap_md}\n\n"
            f"# Задачи и решения\n{insights_text}\n\n"
            f"# Предлагаемые шаги\n{steps_text}"
        )

        obj.analysis_text = analysis_text
        await db.commit()
        await db.refresh(obj)
        await track_usage(db, _user.id, "llm_call", 1.0)
        return _to_dict(obj)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Share endpoints ──────────────────────────────────────────────────

@router.post("/saved-transcriptions/{transcription_id}/share")
async def create_share(
    transcription_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")

    if obj.share_id:
        return ShareResponse(share_id=obj.share_id, share_url=f"/share/{obj.share_id}")

    obj.share_id = str(uuid.uuid4())
    await db.commit()
    return ShareResponse(share_id=obj.share_id, share_url=f"/share/{obj.share_id}")


@router.delete("/saved-transcriptions/{transcription_id}/share")
async def revoke_share(
    transcription_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.id == transcription_id,
            SavedTranscription.user_id == _user.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Transcription not found")

    obj.share_id = None
    await db.commit()
    return {"detail": "Share revoked"}


# ── Public share endpoint (NO AUTH) ─────────────────────────────────

@router.get("/share/{share_id}")
async def get_shared_transcription(
    share_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedTranscription).where(
            SavedTranscription.share_id == share_id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Shared transcription not found")

    return PublicShareResponse(
        title=obj.title,
        full_text=obj.full_text,
        analysis_text=obj.analysis_text,
        segments_json=obj.segments_json,
        speaker_names=obj.speaker_names,
        duration=obj.duration,
        language=obj.language,
        created_at=obj.created_at,
    )
