import asyncio
import json
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from gigaam_transcriber.autoflow import AutoflowStage, StageEvent, run_autoflow
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.limits import check_limit
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

        cancelled = asyncio.Event()

        async def watch_controls() -> None:
            try:
                while True:
                    msg = await ws.receive()
                    if msg.get("type") != "websocket.receive":
                        cancelled.set()
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
                cancelled.set()
            except Exception:
                logger.debug("autoflow control watcher stopped", exc_info=True)
                cancelled.set()

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

        watch_task.cancel()
        result = work_task.result()

        if result.transcription_result is not None:
            duration_minutes = (result.transcription_result.duration or 0.0) / 60
            async with async_session_factory() as db:
                await track_usage(db, identity.user_id, "transcription_minutes", duration_minutes)
                await track_usage(db, identity.user_id, "file_upload", 1.0)
                await db.commit()

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
