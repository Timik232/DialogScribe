import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from gigaam_transcriber.autoflow import AutoflowStage, StageEvent, run_autoflow
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.models import SavedTranscription
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig
from gigaam_transcriber.usage import track_usage
from routers.correlation import get_correlation_id
from gigaam_transcriber.ws_protocol import (
    WS_PROTOCOL_VERSION,
    BinaryUpload,
    OutboundChannel,
    authenticate_websocket,
    close_ws,
    ws_db_session,
)
from routers._helpers import SUPPORTED_EXTENSIONS, _map_diarization

logger = logging.getLogger("dialogscribe-autoflow")
router = APIRouter(prefix="/api/autoflow", tags=["autoflow"])

# Detached autoflow continuations (client gone mid-work). Kept in a module
# level set purely so the event loop retains a strong reference to tasks that
# no coroutine is awaiting; the done-callback removes them on completion.
_DETACHED_TASKS: set[asyncio.Task] = set()


async def _persist_autoflow_result(
    app: Any,
    user_id: str,
    result: Any,
    session_id: str,
) -> SavedTranscription | None:
    """Persist a finished autoflow run as a SavedTranscription row.

    The transcription text becomes durable server-side regardless of whether
    the client is still connected to receive the WS terminal event. Analysis
    artifacts (summary/mindmap/insights/steps) are stored as analysis_text.
    Failures are logged and swallowed: persistence must never break delivery
    of an already-successful result to a live client.
    """
    tr = getattr(result, "transcription_result", None)
    if tr is None or not getattr(tr, "text", None):
        return None

    def _seg_json(s: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": s.text,
            "start": s.start,
            "end": s.end,
        }
        if s.speaker is not None:
            payload["speaker"] = s.speaker
        if s.confidence is not None:
            payload["confidence"] = s.confidence
        return payload

    segments = [_seg_json(s) for s in tr.segments]
    now = datetime.utcnow()
    mins = int(tr.duration or 0) // 60
    secs = int(tr.duration or 0) % 60
    title = f"Autoflow {now.strftime('%d.%m.%Y %H:%M')} — {mins}m {secs}s"

    analysis_parts: list[str] = []
    if getattr(result, "summary_text", None):
        analysis_parts.append(f"# Сводка\n{result.summary_text}")
    if getattr(result, "mindmap_md", None):
        analysis_parts.append(f"# Интеллект-карта\n{result.mindmap_md}")
    items = getattr(result, "action_items", None)
    if items:
        analysis_parts.append(
            "# Задачи\n" + "\n".join(f"- {item}" for item in items)
        )
    steps = getattr(result, "suggested_steps", None)
    if steps:
        analysis_parts.append(
            "# Предлагаемые шаги\n"
            + "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps))
        )
    analysis_text = "\n\n".join(analysis_parts) if analysis_parts else None

    obj = SavedTranscription(
        user_id=user_id,
        title=title,
        full_text=tr.text,
        segments_json=segments,
        speaker_names={},
        duration=tr.duration or 0.0,
        language=tr.language or "ru",
        analysis_text=analysis_text,
    )
    async with async_session_factory() as db:
        db.add(obj)
        await db.commit()
    logger.info(
        "Autoflow session %s persisted as saved transcription %s",
        session_id,
        getattr(obj, "id", "?"),
    )
    return obj


def _get_ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


@router.websocket("/ws")
async def autoflow_ws(ws: WebSocket):
    session_id = str(uuid.uuid4())
    async with ws_db_session() as db:
        identity = await authenticate_websocket(ws, db)
    if identity is None:
        return

    channel = OutboundChannel(ws)
    await channel.start()
    await channel.send(
        {"type": "auth_ok", "protocol": WS_PROTOCOL_VERSION, "session_id": session_id}
    )

    upload: BinaryUpload | None = None
    try:
        upload = BinaryUpload(ws, channel=channel)
        meta = await upload.receive_meta()
        if meta is None:
            return

        filename = str(meta.get("filename") or "audio.wav")
        ext = _get_ext(filename)
        if ext not in SUPPORTED_EXTENSIONS:
            await channel.close_terminal(
                {
                    "type": "error",
                    "stage": "error",
                    "session_id": session_id,
                    "message": f"Неподдерживаемый формат: {ext}",
                }
            )
            return

        upload.suffix = ext  # downstream stage pipeline sniffs the container by extension
        if not await upload.receive_binary():
            return

        channel.emit(
            {
                "type": "status",
                "stage": AutoflowStage.UPLOAD.value,
                "progress": 0.02,
                "message": "Загрузка файла завершена",
            }
        )

        template_key = str(meta.get("template_key") or "meeting")
        diarization_mode = meta.get("diarization_mode", "none")
        include_summary = bool(meta.get("include_summary", True))
        include_mindmap = bool(meta.get("include_mindmap", True))
        include_insights = bool(meta.get("include_insights", False))
        model = str(meta.get("model") or "") or None
        denoise = meta.get("denoise", "none")

        llm_client = LLMClient(LLMClientConfig())
        transcriber = ws.app.state.transcriber

        config = {
            "diarization": _map_diarization(diarization_mode),
            "include_summary": include_summary,
            "include_mindmap": include_mindmap,
            "denoise": denoise,
        }

        def progress_callback(event: StageEvent) -> None:
            payload = {
                "type": "progress",
                "stage": event.stage.value,
                "progress": event.progress,
                "message": event.message,
            }
            if event.skipped_stage is not None:
                payload["skipped_stage"] = event.skipped_stage.value
            channel.emit(payload)

        try:
            async with async_session_factory() as db:
                await check_limit(db, identity.user_id, "transcription_minutes")
        except HTTPException as e:
            await channel.close_terminal(
                {
                    "type": "error",
                    "stage": "error",
                    "session_id": session_id,
                    "code": "limit_exceeded",
                    "message": f"Превышен лимит использования: {e.detail}",
                }
            )
            return

        # A transport disconnect (screen off, app killed, network drop) is NOT
        # a user cancellation: the work keeps running and the result is
        # persisted so it can be picked up later. Only an explicit
        # {"type": "cancel"} control frame cancels.
        cancelled = asyncio.Event()
        client_gone = asyncio.Event()

        async def watch_controls() -> None:
            try:
                while True:
                    msg = await ws.receive()
                    if msg.get("type") != "websocket.receive":
                        client_gone.set()
                        return
                    if "bytes" in msg:
                        continue
                    try:
                        data = json.loads(msg.get("text") or "")
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("type") == "cancel":
                        cancelled.set()
                        return
            except (WebSocketDisconnect, RuntimeError):
                client_gone.set()
            except Exception:
                logger.debug("autoflow control watcher stopped", exc_info=True)
                client_gone.set()

        async def process():
            async with async_session_factory() as db:
                result = await run_autoflow(
                    file_path=upload.path,
                    template_key=template_key,
                    llm_client=llm_client,
                    config=config,
                    transcriber=transcriber,
                    db=db,
                    user_id=identity.user_id,
                    progress_callback=progress_callback,
                    include_insights=include_insights,
                    include_summary=include_summary,
                    include_mindmap=include_mindmap,
                    model=model,
                )
            return result

        work_task = asyncio.create_task(process(), name=f"autoflow-work-{session_id}")
        watch_task = asyncio.create_task(watch_controls(), name=f"autoflow-watch-{session_id}")
        await asyncio.wait({work_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)

        if cancelled.is_set() and not work_task.done():
            work_task.cancel()
            try:
                await work_task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("Autoflow session %s cancelled by client", session_id)
            return

        if client_gone.is_set() and not work_task.done():
            # Transport died mid-work: keep the job running detached. When it
            # finishes, the result is persisted (see _persist_autoflow_result)
            # so the user can retrieve it from the transcriptions list later.
            logger.info(
                "Autoflow session %s: client disconnected, work continues detached",
                session_id,
            )
            watch_task.cancel()
            app_ref = ws.app

            async def _finish_detached() -> None:
                try:
                    result = await work_task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Detached autoflow session %s failed", session_id)
                    return
                try:
                    await _persist_autoflow_result(app_ref, identity.user_id, result, session_id)
                except Exception:
                    logger.exception(
                        "Failed to persist detached autoflow session %s", session_id
                    )

            detached = asyncio.create_task(
                _finish_detached(), name=f"autoflow-detached-{session_id}"
            )
            _DETACHED_TASKS.add(detached)
            detached.add_done_callback(_DETACHED_TASKS.discard)
            return

        watch_task.cancel()
        result = work_task.result()

        if result.transcription_result is not None:
            duration_minutes = (result.transcription_result.duration or 0.0) / 60
            async with async_session_factory() as db:
                await track_usage(db, identity.user_id, "transcription_minutes", duration_minutes)
                await track_usage(db, identity.user_id, "file_upload", 1.0)
                await db.commit()

        await _persist_autoflow_result(ws.app, identity.user_id, result, session_id)

        response_data: dict = {
            "errors": result.errors,
            "stage_timings": result.stage_timings,
        }

        if result.transcription_result:
            tr = result.transcription_result
            response_data["transcription"] = {
                "text": tr.text,
                "language": tr.language,
                "duration": tr.duration,
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text,
                        **({"speaker": s.speaker} if s.speaker is not None else {}),
                        **({"confidence": s.confidence} if s.confidence is not None else {}),
                    }
                    for s in tr.segments
                ],
            }

        if result.summary_text:
            response_data["summary"] = result.summary_text

        if result.mindmap_md:
            response_data["mindmap_md"] = result.mindmap_md

        if result.action_items:
            response_data["action_items"] = result.action_items

        if result.suggested_steps:
            response_data["suggested_steps"] = result.suggested_steps

        await channel.close_terminal(
            {
                "type": "complete",
                "stage": AutoflowStage.COMPLETE.value,
                "session_id": session_id,
                "result": response_data,
            }
        )

    except WebSocketDisconnect:
        logger.info("Autoflow WebSocket disconnected")
    except Exception as e:
        logger.exception(
            "Autoflow WebSocket error (correlation_id=%s)", get_correlation_id()
        )
        try:
            await channel.close_terminal(
                {
                    "type": "error",
                    "stage": "error",
                    "session_id": session_id,
                    "code": "internal_error",
                    "message": "Internal server error",
                    "correlation_id": get_correlation_id(),
                }
            )
        except Exception:
            logger.debug("failed to deliver terminal error event", exc_info=True)
            await close_ws(ws, 1011)
    finally:
        if upload and upload.path and os.path.exists(upload.path):
            try:
                os.unlink(upload.path)
            except OSError:
                pass
        await channel.shutdown()
        await close_ws(ws, 1000)
