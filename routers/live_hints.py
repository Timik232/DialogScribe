import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Literal, cast

import sqlalchemy
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gigaam_transcriber.accumulator import SessionAccumulator
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.event_detector import EventDetector
from gigaam_transcriber.exceptions import ASRError, AudioProcessingError
from gigaam_transcriber.live_hints_models import (
    ErrorMessage,
    FeedbackAckMessage,
    HintFeedbackMessage,
    HintMessage,
    SessionConfigMessage,
    StatusMessage,
    TranscriptMessage,
)
from gigaam_transcriber.live_hints_service import (
    HINT_TEMPLATES,
    AudioAdapter,
    LiveHintsBounds,
    generate_hints,
)
from gigaam_transcriber.llm_cascade import LLMCascade
from gigaam_transcriber.meeting_brief_models import BriefUpdateMessage
from gigaam_transcriber.models import UserSettings
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig
from gigaam_transcriber.ws_protocol import (
    WS_PROTOCOL_VERSION,
    OutboundChannel,
    StreamSession,
    WsProtocolError,
    authenticate_websocket,
    close_ws,
    ws_db_session,
)

logger = logging.getLogger("dialogscribe-live-hints")
router = APIRouter(prefix="/api/live-hints", tags=["live-hints"])

MAX_ASR_RETRIES = 3

_DROP_LOG_INTERVAL = 5.0


class _SessionStats:
    """Counters shared by the receive, dispatch and processor coroutines."""

    def __init__(self) -> None:
        self.chunks_received = 0
        self.chunks_processed = 0
        self.dropped_chunks = 0
        self.last_drop_log = 0.0
        self.last_status_ts = 0.0


# ─── REST endpoints ────────────────────────────────────────────


@router.get("/templates")
async def get_templates():
    """Return available hint templates."""
    return [
        {"slug": key, "label": val["label"]}
        for key, val in HINT_TEMPLATES.items()
    ]


# ─── WebSocket ─────────────────────────────────────────────────


@router.websocket("/ws")
async def live_hints_ws(ws: WebSocket):
    async with ws_db_session() as db:
        identity = await authenticate_websocket(ws, db)
    if identity is None:
        return

    channel = OutboundChannel(ws)
    await channel.start()
    await channel.send({"type": "auth_ok", "protocol": WS_PROTOCOL_VERSION})

    # ── Per-session client instances ───────────────────────────
    user_id = identity.user_id
    provider_preference: str | None = None
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
        logger.warning("Failed to load ASR provider preference for user", exc_info=True)

    bounds = LiveHintsBounds.from_env()
    audio_adapter = AudioAdapter(provider_preference=provider_preference)
    llm_client = LLMClient(LLMClientConfig())

    # ── Live Advisor Agent components ──────────────────────────
    event_detector = EventDetector()
    accumulator = SessionAccumulator()
    cascade = LLMCascade()

    state = SimpleNamespace(
        session_config=None,
        last_hint_time=0.0,
    )
    transcript_segments: list[str] = []
    max_transcript_segments = bounds.max_segments
    stats = _SessionStats()

    recv_queue: asyncio.Queue = asyncio.Queue(maxsize=bounds.recv_queue)
    process_queue: asyncio.Queue = asyncio.Queue(maxsize=bounds.process_queue)

    def _note_drop() -> None:
        stats.dropped_chunks += 1
        now = time.monotonic()
        if now - stats.last_drop_log >= _DROP_LOG_INTERVAL:
            stats.last_drop_log = now
            logger.warning(
                "live-hints recv queue full (recv=%d process=%d); dropped oldest chunk, total dropped=%d",
                recv_queue.qsize(), process_queue.qsize(), stats.dropped_chunks,
            )

    def _enqueue(item: tuple) -> None:
        """FIFO enqueue with drop-oldest overflow; drops are always counted."""
        if item[0] == "audio":
            stats.chunks_received += 1
        try:
            recv_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            _note_drop()
            recv_queue.put_nowait(item)

    async def _dispatch() -> None:
        item = None
        try:
            while True:
                item = await recv_queue.get()
                await process_queue.put(item)
                item = None
        except asyncio.CancelledError:
            if item is not None:
                _note_drop()
            raise

    async def _send_hint_message(hint) -> bool:
        """Send one cascade Hint on the channel; returns True when sent."""
        if hint is None or accumulator.check_duplicate_hint(hint.text):
            return False
        accumulator.add_hint(hint)
        await channel.send(
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
        return True

    async def _process_audio(source: str, audio: bytes) -> None:
        speaker = "user" if source == "mic" else "opponent"
        prefix = "[Вы]:" if speaker == "user" else "[Оппонент]:"

        _asr_start = time.time()
        text: str | None = None
        skip_chunk = False

        for attempt in range(MAX_ASR_RETRIES):
            try:
                text = await audio_adapter.process_chunk_bytes(audio, source)
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
                    await channel.send(
                        ErrorMessage(code="asr", message="Ошибка распознавания речи").model_dump()
                    )
                    skip_chunk = True
            except AudioProcessingError as e:
                logger.warning("Audio processing error: %s", e)
                await channel.send(
                    ErrorMessage(code="asr", message="Ошибка обработки аудио").model_dump()
                )
                skip_chunk = True
                break

        logger.info("ASR processing time: %.2fs for source=%s", time.time() - _asr_start, source)
        stats.chunks_processed += 1

        if skip_chunk:
            return

        if not text or not text.strip():
            await channel.send(
                StatusMessage(status="silent_chunk").model_dump()
            )
            return

        # ── Append to transcript ───────────────────────
        now = time.time()
        segment = f"{prefix} {text.strip()}"
        transcript_segments.append(segment)
        if len(transcript_segments) > max_transcript_segments:
            del transcript_segments[: len(transcript_segments) - max_transcript_segments]

        await channel.send(
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
        if trigger_event and event_detector.should_trigger(trigger_event) and (now - state.last_hint_time) >= min_cascade_interval:
            event_detector.mark_event_processed()
            event_detector.reset_timer()

            context_summary = accumulator.get_context_summary()
            feedback_bias = accumulator.get_feedback_bias_text()
            transcript_window = "\n".join(transcript_segments[-20:])

            try:
                hint = await asyncio.to_thread(
                    cascade.run,
                    transcript_window,
                    context_summary,
                    feedback_bias,
                )
                cascade_hint_sent = await _send_hint_message(hint)
                if cascade_hint_sent:
                    state.last_hint_time = now
            except Exception as e:
                logger.warning("Cascade hint generation error: %s", e)

        # ── Backward-compat: old generate_hints fallback ──
        if (
            not cascade_hint_sent
            and state.session_config
            and now - state.last_hint_time >= 15.0
        ):
            state.last_hint_time = now
            full_transcript = "\n".join(transcript_segments)
            try:
                hints = await asyncio.to_thread(
                    generate_hints,
                    full_transcript,
                    state.session_config["template_key"],
                    llm_client,
                    state.session_config["context_text"],
                )
                for hint in hints:
                    await channel.send(
                        HintMessage(
                            hint_type=cast(Literal["argumentative", "navigational"], hint.get("hint_type", "argumentative")),
                            text=hint.get("text", ""),
                            priority=cast(Literal["critical", "high", "medium", "low"], hint.get("priority", "medium")),
                        ).model_dump()
                    )
            except Exception as e:
                logger.warning("Hint generation error: %s", e)
                await channel.send(
                    ErrorMessage(code="hints", message=str(e)).model_dump()
                )

    async def _process_hint_request() -> None:
        if not state.session_config:
            await channel.send(
                ErrorMessage(
                    code="no_config",
                    message="Session not configured",
                ).model_dump()
            )
            return

        full_transcript = "\n".join(transcript_segments)
        if not full_transcript.strip():
            return

        try:
            context_summary = accumulator.get_context_summary()
            feedback_bias = accumulator.get_feedback_bias_text()
            transcript_window = "\n".join(transcript_segments[-20:])

            hint = await asyncio.to_thread(
                cascade.run,
                transcript_window,
                context_summary,
                feedback_bias,
            )

            if await _send_hint_message(hint):
                state.last_hint_time = time.time()
                return

            hints = await asyncio.to_thread(
                generate_hints,
                full_transcript,
                state.session_config["template_key"],
                llm_client,
                state.session_config["context_text"],
            )
            for h in hints:
                await channel.send(
                    HintMessage(
                        hint_type=cast(Literal["argumentative", "navigational"], h.get("hint_type", "argumentative")),
                        text=h.get("text", ""),
                        priority=cast(Literal["critical", "high", "medium", "low"], h.get("priority", "medium")),
                    ).model_dump()
                )
            state.last_hint_time = time.time()
        except Exception as e:
            logger.warning("Hint generation error: %s", e)
            await channel.send(
                ErrorMessage(code="hints", message=str(e)).model_dump()
            )

    async def _processor() -> None:
        while True:
            item = await process_queue.get()
            try:
                if item[0] == "audio":
                    await _process_audio(item[1], item[2])
                elif item[0] == "hint_request":
                    await _process_hint_request()
            except Exception:
                logger.exception("live-hints processor item failed")

    dispatcher = asyncio.create_task(_dispatch(), name="lh-dispatch")
    processor = asyncio.create_task(_processor(), name="lh-process")

    stream = StreamSession(ws)

    try:
        # ── Message dispatch loop: ONLY receives + tags + enqueues ──
        while True:
            kind, *rest = await stream.receive()

            if kind == "audio":
                source, audio = rest
                _enqueue(("audio", source, audio))
                continue

            (raw,) = rest
            msg_type = raw.get("type", "")

            if msg_type == "session_config":
                try:
                    cfg = SessionConfigMessage(**raw)
                    state.session_config = {
                        "template_key": cfg.template_key,
                        "context_text": cfg.context_text,
                    }
                    await channel.send(
                        StatusMessage(status="ready").model_dump()
                    )
                except Exception as e:
                    await channel.send(
                        ErrorMessage(code="invalid_config", message=str(e)).model_dump()
                    )

            elif msg_type == "brief_update":
                try:
                    brief_msg = BriefUpdateMessage(**raw)
                    accumulator.update_brief(
                        brief_msg.apply_to(accumulator.meeting_brief)
                    )
                    await channel.send(
                        StatusMessage(status="ready").model_dump()
                    )
                except Exception as e:
                    await channel.send(
                        ErrorMessage(code="invalid_brief", message=str(e)).model_dump()
                    )

            elif msg_type == "hint_feedback":
                try:
                    fb_msg = HintFeedbackMessage(**raw)
                    found = accumulator.record_feedback(fb_msg.hint_id, fb_msg.rating)
                    await channel.send(
                        FeedbackAckMessage(
                            hint_id=fb_msg.hint_id,
                            status="recorded" if found else "not_found",
                        ).model_dump()
                    )
                except Exception as e:
                    await channel.send(
                        ErrorMessage(code="invalid_feedback", message=str(e)).model_dump()
                    )

            elif msg_type == "hint_request":
                _enqueue(("hint_request",))

            else:
                await channel.send(
                    ErrorMessage(
                        code="unknown_type",
                        message=f"Unknown message type: {msg_type}",
                    ).model_dump()
                )

    except WsProtocolError as e:
        logger.warning("Live-hints protocol violation: %s (%s)", e.reason, e.code)
    except WebSocketDisconnect:
        logger.info("Live-hints WebSocket disconnected")
    except Exception as e:
        logger.exception("Live-hints WebSocket error")
        try:
            await channel.close_terminal(
                ErrorMessage(code="server", message=str(e)).model_dump()
            )
        except Exception:
            logger.debug("failed to deliver terminal error event", exc_info=True)
            await close_ws(ws, 1011)
    finally:
        # ── Deterministic shutdown: stop workers, drain, join ──
        dispatcher.cancel()
        processor.cancel()
        await asyncio.gather(dispatcher, processor, return_exceptions=True)
        for q in (recv_queue, process_queue):
            while True:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        try:
            await audio_adapter.close()
        except Exception:
            logger.debug("audio adapter close failed", exc_info=True)
        transcript_segments.clear()
        await channel.shutdown()
        await close_ws(ws, 1000)
