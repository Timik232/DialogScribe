import asyncio
import logging
import time
from typing import Literal, cast

import sqlalchemy
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gigaam_transcriber.auth import decode_token
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.live_hints_models import (
    AudioChunkMessage,
    ErrorMessage,
    FeedbackAckMessage,
    HintFeedbackMessage,
    HintMessage,
    SessionConfigMessage,
    StatusMessage,
    TranscriptMessage,
)
from gigaam_transcriber.meeting_brief_models import BriefUpdateMessage
from gigaam_transcriber.exceptions import ASRError, AudioProcessingError
from gigaam_transcriber.live_hints_service import HINT_TEMPLATES, AudioAdapter, generate_hints
from gigaam_transcriber.models import UserSettings
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig
from gigaam_transcriber.event_detector import EventDetector
from gigaam_transcriber.accumulator import SessionAccumulator
from gigaam_transcriber.llm_cascade import LLMCascade

logger = logging.getLogger("dialogscribe-live-hints")
router = APIRouter(prefix="/api/live-hints", tags=["live-hints"])

MAX_ASR_RETRIES = 3


# ─── REST endpoints ────────────────────────────────────────────


@router.get("/templates")
async def get_templates():
    """Return available hint templates."""
    return [
        {"slug": key, "label": val["label"]}
        for key, val in HINT_TEMPLATES.items()
    ]


def _provider_label(base_url: str) -> str:
    """Friendly provider name derived from an LLM base URL."""
    b = (base_url or "").lower()
    if "mistral" in b:
        return "Mistral"
    if "openai" in b:
        return "OpenAI"
    if b == "gigachat":
        return "GigaChat"
    return base_url or "LLM"


@router.get("/info")
async def get_models_info():
    """Report the currently active models (ASR + Live Advisor LLM) for the UI.

    Introspects the real provider objects rather than hardcoding, so the
    indicator always reflects the live configuration.
    """
    from gigaam_transcriber.asr_provider import get_asr_provider

    info: dict = {"asr": None, "advisor": None}

    try:
        asr = get_asr_provider()
        primary = getattr(asr, "_primary", asr)
        asr_model = getattr(primary, "_model", "") or ""
        info["asr"] = {
            "model": asr_model,
            "label": "GigaAM" if "gigaam" in asr_model.lower() else (asr_model or "ASR"),
            "fallback": getattr(asr, "_secondary_name", None),
        }
    except Exception:
        logger.warning("Failed to introspect ASR provider", exc_info=True)

    try:
        advisor_cfg = LLMCascade()._advisor.config
        info["advisor"] = {
            "provider": _provider_label(advisor_cfg.base_url),
            "model": advisor_cfg.model,
        }
    except Exception:
        logger.warning("Failed to introspect advisor LLM", exc_info=True)

    # Analysis LLM (summary / insights / chat / post-meeting)
    try:
        from gigaam_transcriber.summarizer import create_llm_client

        analysis_cfg = create_llm_client().config
        info["analysis"] = {
            "provider": _provider_label(analysis_cfg.base_url),
            "model": analysis_cfg.model,
        }
    except Exception:
        logger.warning("Failed to introspect analysis LLM", exc_info=True)

    return info


# ─── WebSocket ─────────────────────────────────────────────────


@router.websocket("/ws")
async def live_hints_ws(ws: WebSocket):
    await ws.accept()

    # ── JWT auth via query params ──────────────────────────────
    token = ws.query_params.get("token")
    if not token:
        await ws.send_json(ErrorMessage(code="auth", message="Не авторизован").model_dump())
        await ws.close(code=4001)
        return

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise ValueError("Invalid token type")
    except Exception:
        await ws.send_json(ErrorMessage(code="auth", message="Не авторизован").model_dump())
        await ws.close(code=4001)
        return

    # ── Per-session client instances ───────────────────────────
    user_id: str = payload.get("sub", "")
    provider_preference: str | None = None
    if user_id:
        try:
            async with async_session_factory() as db:
                settings = await db.execute(
                    sqlalchemy.select(UserSettings.asr_provider).where(
                        UserSettings.user_id == user_id
                    )
                )
                row = settings.scalar_one_or_none()
                if row:
                    provider_preference = row
        except Exception:
            logger.warning("Failed to load ASR provider preference for user %s", user_id, exc_info=True)

    audio_adapter = AudioAdapter(provider_preference=provider_preference)
    # Live Advisor uses the OpenAI-compatible client (Mistral), not GigaChat.
    llm_client = LLMClient(LLMClientConfig())
    loop = asyncio.get_event_loop()

    # ── Live Advisor Agent components ──────────────────────────
    event_detector = EventDetector()
    accumulator = SessionAccumulator()
    cascade = LLMCascade()

    session_config: dict[str, str] | None = None
    transcript_segments: list[str] = []
    MAX_TRANSCRIPT_SEGMENTS = 500
    last_hint_time: float = 0.0
    processing: bool = False

    try:
        # ── Message dispatch loop ──────────────────────────────
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == "session_config":
                try:
                    cfg = SessionConfigMessage(**raw)
                    session_config = {
                        "template_key": cfg.template_key,
                        "context_text": cfg.context_text,
                    }
                    await ws.send_json(
                        StatusMessage(status="ready").model_dump()
                    )
                except Exception as e:
                    await ws.send_json(
                        ErrorMessage(code="invalid_config", message=str(e)).model_dump()
                    )

            elif msg_type == "brief_update":
                # ── Update meeting brief from client ────────────
                try:
                    brief_msg = BriefUpdateMessage(**raw)
                    accumulator.update_brief(
                        brief_msg.apply_to(accumulator.meeting_brief)
                    )
                    await ws.send_json(
                        StatusMessage(status="ready").model_dump()
                    )
                except Exception as e:
                    await ws.send_json(
                        ErrorMessage(code="invalid_brief", message=str(e)).model_dump()
                    )

            elif msg_type == "hint_feedback":
                # ── Record user feedback on a hint ──────────────
                try:
                    fb_msg = HintFeedbackMessage(**raw)
                    found = accumulator.record_feedback(fb_msg.hint_id, fb_msg.rating)
                    await ws.send_json(
                        FeedbackAckMessage(
                            hint_id=fb_msg.hint_id,
                            status="recorded" if found else "not_found",
                        ).model_dump()
                    )
                except Exception as e:
                    await ws.send_json(
                        ErrorMessage(code="invalid_feedback", message=str(e)).model_dump()
                    )

            elif msg_type == "audio_chunk":
                # ── Chunk size limit (5 MB) ────────────────────
                if len(raw.get("audio_b64", "")) > 5 * 1024 * 1024:
                    await ws.send_json(
                        ErrorMessage(code="chunk_too_large", message="Аудио чанк слишком большой").model_dump()
                    )
                    continue

                try:
                    chunk = AudioChunkMessage(**raw)
                except Exception as e:
                    await ws.send_json(
                        ErrorMessage(code="invalid_chunk", message=str(e)).model_dump()
                    )
                    continue

                # ── Backpressure: drop chunk while previous one is still processing ─
                # Prevents unbounded queue growth and memory pressure on the server.
                if processing:
                    await ws.send_json(
                        StatusMessage(status="processing").model_dump()
                    )
                    continue

                # ── Map source → speaker ───────────────────────
                speaker = "user" if chunk.source == "mic" else "opponent"
                prefix = "[Вы]:" if speaker == "user" else "[Оппонент]:"

                processing = True
                _asr_start = time.time()
                try:
                    text: str | None = None
                    skip_chunk = False

                    for attempt in range(MAX_ASR_RETRIES):
                        try:
                            text = await audio_adapter.process_chunk(
                                chunk.audio_b64, chunk.source
                            )
                            break
                        except ASRError as e:
                            if attempt < MAX_ASR_RETRIES - 1:
                                delay = 1 * (2 ** attempt)
                                logger.warning(
                                    "ASR retry %d/%d in %.1fs: %s",
                                    attempt + 1, MAX_ASR_RETRIES, delay, e,
                                )
                                await asyncio.sleep(delay)
                            else:
                                logger.warning(
                                    "ASR failed after %d attempts: %s",
                                    MAX_ASR_RETRIES, e,
                                )
                                await ws.send_json(
                                    ErrorMessage(code="asr", message="Ошибка распознавания речи").model_dump()
                                )
                                skip_chunk = True
                        except AudioProcessingError as e:
                            logger.warning("Audio processing error: %s", e)
                            await ws.send_json(
                                ErrorMessage(code="asr", message="Ошибка обработки аудио").model_dump()
                            )
                            skip_chunk = True
                            break

                    logger.info("ASR processing time: %.2fs for source=%s", time.time() - _asr_start, chunk.source)

                    if skip_chunk:
                        continue

                    if not text or not text.strip():
                        await ws.send_json(
                            StatusMessage(status="silent_chunk").model_dump()
                        )
                        continue

                    # ── Append to transcript ───────────────────────
                    now = time.time()
                    segment = f"{prefix} {text.strip()}"
                    transcript_segments.append(segment)
                    if len(transcript_segments) > MAX_TRANSCRIPT_SEGMENTS:
                        transcript_segments = transcript_segments[-MAX_TRANSCRIPT_SEGMENTS:]

                    await ws.send_json(
                        TranscriptMessage(
                            text=text.strip(),
                            speaker=speaker,
                            timestamp=now,
                        ).model_dump()
                    )

                    # ── Live Advisor: extract facts & update phase ──
                    accumulator.extract_facts_from_text(text.strip(), now)
                    accumulator.update_phase_from_text(text.strip())

                    # ── Live Advisor: event detection ───────────────
                    pause_event = event_detector.check_pause(now)
                    keyword_events = event_detector.process_transcript_chunk(text.strip())
                    timer_fired = event_detector.should_trigger_timer(now)

                    # Determine trigger event (priority: keyword > pause > timer)
                    trigger_event = None
                    if keyword_events:
                        trigger_event = keyword_events[0]
                    elif pause_event:
                        trigger_event = pause_event
                    elif timer_fired:
                        trigger_event = event_detector.create_timer_event(
                            "\n".join(transcript_segments[-20:])
                        )

                    # ── Live Advisor: cascade hint generation ───────
                    cascade_hint_sent = False
                    min_cascade_interval = 8.0
                    if trigger_event and event_detector.should_trigger(trigger_event) and (now - last_hint_time) >= min_cascade_interval:
                        event_detector.mark_event_processed()
                        event_detector.reset_timer()

                        context_summary = accumulator.get_context_summary()
                        feedback_bias = accumulator.get_feedback_bias_text()
                        transcript_window = "\n".join(transcript_segments[-20:])

                        try:
                            hint = await loop.run_in_executor(
                                None,
                                cascade.run,
                                transcript_window,
                                context_summary,
                                feedback_bias,
                            )

                            if hint is not None:
                                if not accumulator.check_duplicate_hint(hint.text):
                                    accumulator.add_hint(hint)
                                    await ws.send_json(
                                        HintMessage(
                                            hint_type=cast(
                                                Literal["argumentative", "navigational", "tactical", "strategic", "warning", "analytical"],
                                                hint.type.value,
                                            ),
                                            text=hint.text,
                                            priority=cast(
                                                Literal["critical", "high", "medium", "low"],
                                                hint.priority.value if hint.priority.value in ("critical", "high", "medium", "low") else "medium",
                                            ),
                                            hint_id=hint.hint_id,
                                            rationale=hint.rationale,
                                        ).model_dump()
                                    )
                                    last_hint_time = now
                                    cascade_hint_sent = True
                        except Exception as e:
                            logger.warning("Cascade hint generation error: %s", e)

                    # ── Backward-compat: old generate_hints fallback ──
                    if (
                        not cascade_hint_sent
                        and session_config
                        and now - last_hint_time >= 15.0
                    ):
                        last_hint_time = now
                        full_transcript = "\n".join(transcript_segments)
                        try:
                            hints = await loop.run_in_executor(
                                None,
                                generate_hints,
                                full_transcript,
                                session_config["template_key"],
                                llm_client,
                                session_config["context_text"],
                            )
                            for hint in hints:
                                await ws.send_json(
                                    HintMessage(
                                        hint_type=cast(Literal["argumentative", "navigational"], hint.get("hint_type", "argumentative")),
                                        text=hint.get("text", ""),
                                        priority=cast(Literal["critical", "high", "medium", "low"], hint.get("priority", "medium")),
                                    ).model_dump()
                                )
                        except Exception as e:
                            logger.warning("Hint generation error: %s", e)
                            await ws.send_json(
                                ErrorMessage(code="hints", message=str(e)).model_dump()
                            )
                finally:
                    processing = False

            elif msg_type == "hint_request":
                if not session_config:
                    await ws.send_json(
                        ErrorMessage(
                            code="no_config",
                            message="Session not configured",
                        ).model_dump()
                    )
                    continue

                full_transcript = "\n".join(transcript_segments)
                if not full_transcript.strip():
                    continue

                try:
                    # ── Live Advisor: try cascade first ────────────
                    context_summary = accumulator.get_context_summary()
                    feedback_bias = accumulator.get_feedback_bias_text()
                    transcript_window = "\n".join(transcript_segments[-20:])

                    hint = await loop.run_in_executor(
                        None,
                        cascade.run,
                        transcript_window,
                        context_summary,
                        feedback_bias,
                    )

                    if hint is not None and not accumulator.check_duplicate_hint(hint.text):
                        accumulator.add_hint(hint)
                        await ws.send_json(
                            HintMessage(
                                hint_type=cast(
                                    Literal["argumentative", "navigational", "tactical", "strategic", "warning", "analytical"],
                                    hint.type.value,
                                ),
                                text=hint.text,
                                priority=cast(
                                    Literal["critical", "high", "medium", "low"],
                                    hint.priority.value if hint.priority.value in ("critical", "high", "medium", "low") else "medium",
                                ),
                                hint_id=hint.hint_id,
                                rationale=hint.rationale,
                            ).model_dump()
                        )
                        last_hint_time = time.time()
                    else:
                        # ── Backward-compat: old generate_hints fallback ──
                        hints = await loop.run_in_executor(
                            None,
                            generate_hints,
                            full_transcript,
                            session_config["template_key"],
                            llm_client,
                            session_config["context_text"],
                        )
                        for h in hints:
                            await ws.send_json(
                                HintMessage(
                                    hint_type=cast(Literal["argumentative", "navigational"], h.get("hint_type", "argumentative")),
                                    text=h.get("text", ""),
                                    priority=cast(Literal["critical", "high", "medium", "low"], h.get("priority", "medium")),
                                ).model_dump()
                            )
                        last_hint_time = time.time()
                except Exception as e:
                    logger.warning("Hint generation error: %s", e)
                    await ws.send_json(
                        ErrorMessage(code="hints", message=str(e)).model_dump()
                    )

            else:
                await ws.send_json(
                    ErrorMessage(
                        code="unknown_type",
                        message=f"Unknown message type: {msg_type}",
                    ).model_dump()
                )

    except WebSocketDisconnect:
        logger.info("Live-hints WebSocket disconnected")
    except Exception as e:
        logger.exception("Live-hints WebSocket error")
        try:
            await ws.send_json(
                ErrorMessage(code="server", message=str(e)).model_dump()
            )
        except Exception:
            pass
    finally:
        # ── Cleanup ────────────────────────────────────────────
        try:
            await audio_adapter.close()
        except Exception:
            pass
        transcript_segments.clear()
        try:
            await ws.close()
        except Exception:
            pass
