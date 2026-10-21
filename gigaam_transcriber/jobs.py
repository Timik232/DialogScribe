"""Background transcription jobs — service layer.

Implements docs/design/background-jobs.md phase 1: transcription runs in a
server-side asyncio task whose state lives in the ``transcription_jobs``
table, decoupled from any client connection. Clients submit ``POST /api/jobs``
and poll ``GET /api/jobs/{id}``; a disconnect (screen off, app killed) does
not cancel or lose the work, and the finished result is auto-saved into
``saved_transcriptions`` so it appears in the history.

Concurrency model: one asyncio task per job in the API process (MVP — no
external queue). A module-level semaphore bounds concurrent transcriptions
(GPU/ASR protection); extra jobs wait in ``queued`` state. Job input files
live under ``JOB_STORE_DIR`` (data volume) and are removed when the job
reaches a terminal state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid as uuid_lib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.models import TranscriptionJob

logger = logging.getLogger("dialogscribe-jobs")

JOB_STORE_DIR = Path(
    os.getenv("JOB_STORE_DIR", "/app/data/jobs")
    if os.getenv("JOB_STORE_DIR")
    else str(Path("data") / "jobs")
)

_MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
_job_semaphore: asyncio.Semaphore | None = None
_task_registry: set[asyncio.Task] = set()


def _semaphore() -> asyncio.Semaphore:
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_JOBS)
    return _job_semaphore


def _validate_upload_filename(filename: str | None) -> str:
    """Basename-only, bounded-length filename for storage."""
    name = (filename or "").strip()
    if not name or len(name) > 255:
        raise ValueError("Invalid filename")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("Invalid filename")
    return name


def ensure_job_store() -> Path:
    JOB_STORE_DIR.mkdir(parents=True, exist_ok=True)
    return JOB_STORE_DIR


def new_job_id() -> str:
    return str(uuid_lib.uuid4())


def job_input_path(job_id: str, filename: str) -> Path:
    return ensure_job_store() / f"{job_id}{Path(filename).suffix.lower()}"


async def create_job(
    user_id: str,
    *,
    input_path: str,
    input_filename: str | None,
    input_size_bytes: int | None,
    params: dict[str, Any],
) -> TranscriptionJob:
    """Insert a queued job row and return it (committed)."""
    job = TranscriptionJob(
        id=new_job_id(),
        user_id=user_id,
        status="queued",
        stage="upload",
        progress=0.0,
        params=params,
        input_filename=input_filename,
        input_path=str(input_path),
        input_size_bytes=input_size_bytes,
    )
    async with async_session_factory() as db:
        db.add(job)
        await db.commit()
        await db.refresh(job)
    return job


async def update_job(
    job_id: str,
    *,
    session: AsyncSession | None = None,
    **fields: Any,
) -> None:
    """Update job fields; no-ops for terminal jobs (state machine guard)."""
    stmt = select(TranscriptionJob).where(TranscriptionJob.id == job_id).with_for_update()
    if session is not None:
        obj = (await session.execute(stmt)).scalar_one_or_none()
        await _apply_fields(obj, fields)
        await session.flush()
        return
    async with async_session_factory() as db:
        obj = (await db.execute(stmt)).scalar_one_or_none()
        await _apply_fields(obj, fields)
        await db.commit()


async def _apply_fields(obj: TranscriptionJob | None, fields: dict[str, Any]) -> None:
    if obj is None:
        return
    if obj.status in TranscriptionJob.TERMINAL_STATUSES:
        return
    for key, value in fields.items():
        setattr(obj, key, value)


async def get_job(job_id: str, user_id: str) -> TranscriptionJob | None:
    async with async_session_factory() as db:
        obj = (
            await db.execute(
                select(TranscriptionJob).where(
                    TranscriptionJob.id == job_id,
                    TranscriptionJob.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        # Detach a fully-loaded copy for safe attribute access after close.
        if obj is not None:
            await db.refresh(obj)
        return obj


async def list_jobs(user_id: str, *, active_only: bool = False) -> list[TranscriptionJob]:
    async with async_session_factory() as db:
        stmt = select(TranscriptionJob).where(TranscriptionJob.user_id == user_id)
        if active_only:
            stmt = stmt.where(TranscriptionJob.status.notin_(TranscriptionJob.TERMINAL_STATUSES))
        stmt = stmt.order_by(TranscriptionJob.created_at.desc()).limit(100)
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            await db.refresh(row)
        return list(rows)


def job_status_payload(job: TranscriptionJob) -> dict[str, Any]:
    """Public JSON shape returned by the jobs API."""
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "filename": job.input_filename,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "updated_at": job.updated_at.isoformat() + "Z" if job.updated_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "error": job.error,
        "saved_transcription_id": job.saved_transcription_id,
        "result": job.result if job.status == "completed" else None,
    }


async def cancel_job(job_id: str, user_id: str) -> dict[str, Any]:
    """Mark a job cancelled; returns a status payload."""
    async with async_session_factory() as db:
        stmt = (
            select(TranscriptionJob)
            .where(
                TranscriptionJob.id == job_id,
                TranscriptionJob.user_id == user_id,
            )
            .with_for_update()
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if job is None:
            return {"error": "not_found"}
        if job.status in TranscriptionJob.TERMINAL_STATUSES:
            return job_status_payload(job)
        job.status = "cancelled"
        job.stage = "cancelled"
        job.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(job)
        payload = job_status_payload(job)
    _remove_input_file_quietly(job.input_path)
    return payload


def _remove_input_file_quietly(path: str | None) -> None:
    if not path:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)


async def _run_job(
    job_id: str,
    user_id: str,
    transcriber: Any,
    progress_adapter: Callable[[int, int, str], None] | None = None,
) -> None:
    """Process one job to a terminal state. Never raises."""
    from gigaam_transcriber.usage import track_usage
    from routers._persistence import persist_transcription_result

    started = time.monotonic()
    await update_job(job_id, status="processing", stage="transcribing", started_at=datetime.utcnow(), progress=0.05)

    async with _semaphore():
        # Re-read the job: it may have been cancelled while queued.
        job = await get_job(job_id, user_id)
        if job is None or job.status in TranscriptionJob.TERMINAL_STATUSES:
            _remove_input_file_quietly(job.input_path if job else None)
            return

        params = job.params or {}
        loop = asyncio.get_running_loop()

        def chunk_progress(done: int, total: int, label: str) -> None:
            if total <= 0:
                return
            frac = min(0.95, 0.05 + 0.9 * (done / total))
            asyncio.run_coroutine_threadsafe(
                update_job(job_id, stage="transcribing", progress=frac), loop
            )

        try:
            result = await asyncio.to_thread(
                transcriber.transcribe,
                input_path=job.input_path,
                diarization=params.get("diarization", "none"),
                language=params.get("language") or "ru",
                denoise=params.get("denoise", "none"),
                provider_preference=params.get("provider_preference"),
            )
        except asyncio.CancelledError:
            await update_job(
                job_id, status="cancelled", stage="cancelled", finished_at=datetime.utcnow()
            )
            _remove_input_file_quietly(job.input_path)
            raise
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            await update_job(
                job_id,
                status="failed",
                stage="error",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.utcnow(),
            )
            _remove_input_file_quietly(job.input_path)
            return

        segments = [
            {
                "text": s.text,
                "start": s.start,
                "end": s.end,
                **({"speaker": s.speaker} if s.speaker is not None else {}),
                **({"confidence": s.confidence} if s.confidence is not None else {}),
            }
            for s in result.segments
        ]
        result_payload = {
            "segments": segments,
            "duration": result.duration,
            "text": result.text,
            "language": result.language,
        }

        await update_job(job_id, stage="saving", progress=0.97)

        saved_id: str | None = None
        try:
            async with async_session_factory() as db:
                saved = await persist_transcription_result(
                    db,
                    user_id,
                    result_payload,
                    origin="job",
                    filename=job.input_filename,
                )
                if saved is not None:
                    await db.commit()
                    saved_id = saved.id
            if saved_id is None:
                logger.warning("Job %s produced no persistable text", job_id)
        except Exception:
            logger.exception("Job %s: auto-save failed", job_id)

        duration_minutes = (result.duration or 0.0) / 60
        try:
            async with async_session_factory() as db:
                await track_usage(db, user_id, "transcription_minutes", duration_minutes)
                await track_usage(db, user_id, "file_upload", 1.0)
                await db.commit()
        except Exception:
            logger.exception("Job %s: usage tracking failed", job_id)

        await update_job(
            job_id,
            status="completed",
            stage="done",
            progress=1.0,
            result=result_payload,
            saved_transcription_id=saved_id,
            finished_at=datetime.utcnow(),
        )
        _remove_input_file_quietly(job.input_path)
        logger.info(
            "Job %s completed in %.1fs (saved_transcription=%s)",
            job_id,
            time.monotonic() - started,
            saved_id,
        )


async def launch_job(job: TranscriptionJob, transcriber: Any) -> asyncio.Task:
    """Spawn the processing task for a freshly created job."""
    task = asyncio.create_task(
        _run_job(job.id, job.user_id, transcriber), name=f"job-{job.id}"
    )
    _task_registry.add(task)
    task.add_done_callback(_task_registry.discard)
    return task


async def recover_stale_jobs() -> int:
    """Startup sweep: fail orphaned processing/queued rows from a previous run."""
    count = 0
    async with async_session_factory() as db:
        stmt = select(TranscriptionJob).where(
            TranscriptionJob.status.in_(["queued", "processing"])
        )
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            row.status = "failed"
            row.stage = "error"
            row.error = "Interrupted by server restart"
            row.finished_at = datetime.utcnow()
            count += 1
        if count:
            await db.commit()
    logger.info("Startup sweep failed %d stale transcription job(s)", count)
    return count
