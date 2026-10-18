"""Background transcription jobs — REST API.

``POST /api/jobs``        upload + create, returns immediately with a job id
``GET /api/jobs``        the user's jobs (active first, recent history)
``GET /api/jobs/{id}``   status/progress/result for polling
``DELETE /api/jobs/{id}`` cancel

The mobile-UX contract (docs/design/background-jobs.md): the client may
disappear at any point — screen off, app killed, network drop — and the
work still finishes on the server, lands in saved-transcriptions, and is
discoverable by polling or from the history page.
"""

from __future__ import annotations

import os
import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.jobs import (
    _validate_upload_filename,
    cancel_job,
    create_job,
    get_job,
    job_input_path,
    job_status_payload,
    launch_job,
    list_jobs,
)
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.models import User
from gigaam_transcriber.rate_limit import user_rate_limit
from routers._helpers import logger
from routers._uploads import spool_upload

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_ALLOWED_DIARIZATION = {"none", "simple", "advanced"}
_ALLOWED_DENOISE = {"none", "light", "medium"}
_MAX_LANG_LEN = 8


def _clean_params(
    diarization_mode: str | None,
    language: str | None,
    denoise: str | None,
    provider_preference: str | None,
) -> dict[str, Any]:
    dio = (diarization_mode or "none").strip().lower()
    if dio not in _ALLOWED_DIARIZATION:
        raise HTTPException(status_code=400, detail="Invalid diarization_mode")
    den = (denoise or "none").strip().lower()
    if den not in _ALLOWED_DENOISE:
        raise HTTPException(status_code=400, detail="Invalid denoise")
    lang = (language or "").strip()
    if len(lang) > _MAX_LANG_LEN:
        raise HTTPException(status_code=400, detail="Invalid language")
    return {
        "diarization": dio,
        "language": lang or None,
        "denoise": den,
        "provider_preference": (provider_preference or "").strip() or None,
    }


@router.post("", status_code=202, dependencies=[Depends(user_rate_limit("upload"))])
async def submit_job(
    request: Request,
    file: Annotated[UploadFile, File()],
    diarization_mode: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    denoise: Annotated[str | None, Form()] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await check_limit(db, user.id, "transcription_minutes")

    try:
        filename = _validate_upload_filename(file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        raise HTTPException(status_code=400, detail="Filename has no extension")

    # spool_upload validates the extension against the shared allowlist and
    # streams to a size-bounded 0600 tempfile; we then move it into the job
    # store (data volume) so it survives the request and any client
    # disconnect. Its finally-unlink becomes a no-op after os.replace.
    try:
        with spool_upload(file) as tmp_path:
            size = os.path.getsize(tmp_path)
            params = _clean_params(diarization_mode, language, denoise, None)
            job = await create_job(
                user.id,
                input_path=str(tmp_path),
                input_filename=filename,
                input_size_bytes=size,
                params=params,
            )
            final_path = job_input_path(job.id, filename)
            # shutil.move, not os.replace: the spool tempfile lives on the
            # container's overlay fs while the job store is a bind mount —
            # os.replace fails with EXDEV there (verified on prod).
            shutil.move(tmp_path, final_path)
    except HTTPException:
        raise
    except OSError as exc:
        logger.exception("Job input storage failed")
        raise HTTPException(status_code=503, detail="Temporary storage unavailable") from exc

    job = await _update_input_path(job.id, str(final_path))

    await launch_job(job, request.app.state.transcriber)
    return job_status_payload(job)


async def _update_input_path(job_id: str, new_path: str):
    from sqlalchemy import select

    from gigaam_transcriber.database import async_session_factory
    from gigaam_transcriber.models import TranscriptionJob

    async with async_session_factory() as db:
        obj = (
            await db.execute(
                select(TranscriptionJob).where(TranscriptionJob.id == job_id)
            )
        ).scalar_one_or_none()
        obj.input_path = new_path
        await db.commit()
        await db.refresh(obj)
        return obj


@router.get("")
async def list_user_jobs(
    active_only: bool = False,
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    jobs = await list_jobs(user.id, active_only=active_only)
    return [job_status_payload(job) for job in jobs]


@router.get("/{job_id}")
async def get_job_status(
    job_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = await get_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_status_payload(job)


@router.delete("/{job_id}")
async def cancel_user_job(
    job_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    payload = await cancel_job(job_id, user.id)
    if payload.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    return payload
