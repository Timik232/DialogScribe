import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gigaam_transcriber.autoflow import run_autoflow
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig
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
    async with ws_db_session() as db:
        identity = await authenticate_websocket(ws, db)
    if identity is None:
        return

    channel = OutboundChannel(ws)
    await channel.start()
    await channel.send({"type": "auth_ok", "protocol": WS_PROTOCOL_VERSION})

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
                {"type": "error", "stage": "error", "message": f"Неподдерживаемый формат: {ext}"}
            )
            return

        upload.suffix = ext  # downstream stage pipeline sniffs the container by extension
        if not await upload.receive_binary():
            return

        channel.emit({"type": "status", "stage": "upload_complete", "progress": 0.02,
                      "message": "Загрузка файла завершена"})

        template_key = str(meta.get("template_key") or "meeting")
        diarization_mode = meta.get("diarization_mode", "none")
        include_summary = bool(meta.get("include_summary", True))
        include_mindmap = bool(meta.get("include_mindmap", True))
        include_insights = bool(meta.get("include_insights", False))
        model = str(meta.get("model") or "")
        denoise = meta.get("denoise", "none")

        llm_config = LLMClientConfig()
        if model:
            llm_config.model = model
        llm_client = LLMClient(llm_config)

        transcriber = ws.app.state.transcriber

        config = {
            "diarization": _map_diarization(diarization_mode),
            "include_summary": include_summary,
            "include_mindmap": include_mindmap,
            "denoise": denoise,
        }

        def progress_callback(message: str, progress: float) -> None:
            stage = "processing"
            if "Транскриб" in message:
                stage = "transcribing"
            elif "саммари" in message.lower() or "Саммари" in message:
                stage = "summarizing"
            elif "инсайт" in message.lower():
                stage = "insights"
            elif "майндмэп" in message.lower() or "Готово" in message:
                stage = "mindmap"
            channel.emit({
                "type": "progress",
                "stage": stage,
                "progress": progress,
                "message": message,
            })

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
            )

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

        await channel.close_terminal({"type": "complete", "stage": "complete", "result": response_data})

    except WebSocketDisconnect:
        logger.info("Autoflow WebSocket disconnected")
    except Exception as e:
        logger.exception("Autoflow WebSocket error")
        try:
            await channel.close_terminal({"type": "error", "stage": "error", "message": str(e)})
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
