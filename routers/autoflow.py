import asyncio
import base64
import logging
import os
import tempfile

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gigaam_transcriber.autoflow import run_autoflow
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig
from gigaam_transcriber.auth import decode_token
from gigaam_transcriber.database import async_session_factory
from routers._helpers import SUPPORTED_EXTENSIONS, _map_diarization

logger = logging.getLogger("dialogscribe-autoflow")
router = APIRouter(prefix="/api/autoflow", tags=["autoflow"])


def _get_ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


@router.websocket("/ws")
async def autoflow_ws(ws: WebSocket):
    await ws.accept()

    # JWT auth: check for token in query params or first message
    token = ws.query_params.get("token")
    if not token:
        await ws.send_json({"stage": "error", "message": "Не авторизован"})
        await ws.close(code=4001)
        return

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise ValueError("Invalid token type")
    except Exception:
        await ws.send_json({"stage": "error", "message": "Не авторизован"})
        await ws.close(code=4001)
        return

    tmp_path: str | None = None

    try:
        raw = await ws.receive_json()
        file_b64 = raw.get("file_data", "")
        filename = raw.get("filename", "audio.wav")
        template_key = raw.get("template_key", "meeting")
        diarization_mode = raw.get("diarization_mode", "none")
        include_summary = raw.get("include_summary", True)
        include_mindmap = raw.get("include_mindmap", True)
        include_insights = raw.get("include_insights", False)
        model = raw.get("model", "")
        denoise = raw.get("denoise", "none")

        if not file_b64:
            await ws.send_json({"stage": "error", "message": "Файл не предоставлен"})
            await ws.close()
            return

        ext = _get_ext(filename)
        if ext not in SUPPORTED_EXTENSIONS:
            await ws.send_json({"stage": "error", "message": f"Неподдерживаемый формат: {ext}"})
            await ws.close()
            return

        file_bytes = base64.b64decode(file_b64)
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)

        llm_config = LLMClientConfig()
        if model:
            llm_config.model = model
        llm_client = LLMClient(llm_config)

        transcriber = ws.app.state.transcriber
        user_id: str = payload.get("sub", "")

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

            asyncio.get_event_loop().create_task(
                ws.send_json({
                    "stage": stage,
                    "progress": progress,
                    "message": message,
                })
            )

        async with async_session_factory() as db:
            result = await run_autoflow(
                file_path=tmp_path,
                template_key=template_key,
                llm_client=llm_client,
                config=config,
                transcriber=transcriber,
                db=db,
                user_id=user_id,
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

        if result.mindmap_html:
            response_data["mindmap_html"] = result.mindmap_html

        if result.mindmap_md:
            response_data["mindmap_md"] = result.mindmap_md

        if result.action_items:
            response_data["action_items"] = result.action_items

        if result.suggested_steps:
            response_data["suggested_steps"] = result.suggested_steps

        await ws.send_json({"stage": "complete", "result": response_data})

    except WebSocketDisconnect:
        logger.info("Autoflow WebSocket disconnected")
    except Exception as e:
        logger.exception("Autoflow WebSocket error")
        try:
            await ws.send_json({"stage": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        try:
            await ws.close()
        except Exception:
            pass
