import asyncio
import logging
import time
from collections import deque
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
from routers.correlation import get_correlation_id

logger = logging.getLogger("dialogscribe-live-hints")
router = APIRouter(prefix="/api/live-hints", tags=["live-hints"])

_DROP_LOG_INTERVAL = 5.0
_TRANSCRIPT_WINDOW = 20
_CASCADE_MIN_INTERVAL = 8.0
_FALLBACK_HINT_INTERVAL = 15.0


# ─── REST endpoints ────────────────────────────────────────────


@router.get("/templates")
async def get_templates():
    """Return available hint templates."""
    return [
        {"slug": key, "label": val["label"]}
        for key, val in HINT_TEMPLATES.items()
    ]


# ─── WebSocket session ─────────────────────────────────────────


class _SessionStats:
    """Counters shared by the receive, dispatch and processor coroutines."""

    def __init__(self) -> None:
        self.chunks_received = 0
        self.chunks_processed = 0
        self.dropped_chunks = 0
        self.last_drop_log = 0.0
        self.last_status_ts = time.monotonic()


async def _load_provider_preference(user_id: str) -> str | None:
    try:
        async with async_session_factory() as db:
            settings = await db.execute(
                sqlalchemy.select(UserSettings.asr_provider).where(
                    UserSettings.user_id == user_id
                )
            )
            row = settings.scalar_one_or_none()
            return row if row else None
    except Exception:
        logger.warning("Failed to load ASR provider preference for user", exc_info=True)
        return None


class _LiveHintsSession:
    """One authenticated live-hints WS connection.

    The receive loop only tags frames and enqueues work into a bounded FIFO
    (drop-oldest, drops always counted); the dispatcher moves items to the
    processor queue; the processor owns ASR, transcript state and hint
    generation. Control frames (config/brief/feedback) are handled inline in
    the receive loop so ordering with enqueued work is preserved.
    """

    def __init__(self, ws: WebSocket, channel: OutboundChannel, provider_preference: str | None) -> None:
        self._ws = ws
        self._channel = channel
        self._bounds = LiveHintsBounds.from_env()
        self._adapter = AudioAdapter(provider_preference=provider_preference)
        self._llm = LLMClient(
            LLMClientConfig(max_retries=max(0, self._bounds.llm_attempts - 1))
        )
        self._event_detector = EventDetector()
        self._accumulator = SessionAccumulator()
        self._cascade = LLMCascade()
        self._session_config: dict | None = None
        self._last_hint_time = 0.0
        self._transcript_segments: deque[str] = deque(maxlen=self._bounds.max_segments)
        self._stats = _SessionStats()
        self._recv_queue: asyncio.Queue = asyncio.Queue(maxsize=self._bounds.recv_queue)
        self._process_queue: asyncio.Queue = asyncio.Queue(maxsize=self._bounds.process_queue)

    # ── status/backpressure ────────────────────────────────────

    def _invalid_count(self) -> int:
        value = getattr(self._adapter, "invalid_chunks", 0)
        return value if isinstance(value, int) else 0

    def _status_payload(self) -> dict:
        caps = self._accumulator.caps.as_dict() if self._accumulator.caps else {}
        caps.update(
            recv_queue=self._bounds.recv_queue,
            process_queue=self._bounds.process_queue,
            max_segments=self._bounds.max_segments,
        )
        return {
            "queues": {"recv": self._recv_queue.qsize(), "process": self._process_queue.qsize()},
            "dropped": self._stats.dropped_chunks,
            "invalid_chunks": self._invalid_count(),
            "accumulators": {
                **self._accumulator.sizes(),
                "segments": len(self._transcript_segments),
            },
            "caps": caps,
        }

    def _emit_backpressure_status(self) -> None:
        self._channel.emit(
            StatusMessage(status="backpressure", **self._status_payload()).model_dump()
        )

    def _maybe_emit_interval_status(self, now: float) -> None:
        if now - self._stats.last_status_ts >= self._bounds.status_interval:
            self._stats.last_status_ts = now
            self._channel.emit(
                StatusMessage(status="processing", **self._status_payload()).model_dump()
            )

    def _note_drop(self) -> None:
        self._stats.dropped_chunks += 1
        now = time.monotonic()
        if now - self._stats.last_drop_log >= _DROP_LOG_INTERVAL:
            self._stats.last_drop_log = now
            logger.warning(
                "live-hints recv queue full (recv=%d process=%d); dropped oldest chunk, total dropped=%d",
                self._recv_queue.qsize(), self._process_queue.qsize(), self._stats.dropped_chunks,
            )
        self._emit_backpressure_status()

    def _enqueue(self, item: tuple) -> None:
        """FIFO enqueue with drop-oldest overflow; drops are always counted."""
        if item[0] == "audio":
            self._stats.chunks_received += 1
        try:
            self._recv_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._note_drop()
            self._recv_queue.put_nowait(item)

    # ── worker coroutines ──────────────────────────────────────

    async def _dispatch(self) -> None:
        item = None
        try:
            while True:
                item = await self._recv_queue.get()
                await self._process_queue.put(item)
                item = None
        except asyncio.CancelledError:
            if item is not None:
                self._note_drop()
            raise

    async def _processor(self) -> None:
        while True:
            item = await self._process_queue.get()
            try:
                self._maybe_emit_interval_status(time.monotonic())
                if item[0] == "audio":
                    await self._process_audio(item[1], item[2])
                elif item[0] == "hint_request":
                    await self._process_hint_request()
            except Exception:
                logger.exception("live-hints processor item failed")

    # ── hint generation ────────────────────────────────────────

    async def _send_hint_message(self, hint) -> bool:
        """Send one cascade Hint on the channel; returns True when sent."""
        if hint is None or self._accumulator.check_duplicate_hint(hint.text):
            return False
        self._accumulator.add_hint(hint)
        await self._channel.send(
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

    async def _send_fallback_hints(self) -> None:
        full_transcript = "\n".join(self._transcript_segments)
        hints = await asyncio.to_thread(
            generate_hints,
            full_transcript,
            self._session_config["template_key"],
            self._llm,
            self._session_config["context_text"],
        )
        for hint in hints:
            await self._channel.send(
                HintMessage(
                    hint_type=cast(Literal["argumentative", "navigational"], hint.get("hint_type", "argumentative")),
                    text=hint.get("text", ""),
                    priority=cast(Literal["critical", "high", "medium", "low"], hint.get("priority", "medium")),
                ).model_dump()
            )

    async def _process_hint_request(self) -> None:
        if not self._session_config:
            await self._channel.send(
                ErrorMessage(
                    code="no_config",
                    message="Session not configured",
                ).model_dump()
            )
            return

        full_transcript = "\n".join(self._transcript_segments)
        if not full_transcript.strip():
            return

        try:
            context_summary = self._accumulator.get_context_summary()
            feedback_bias = self._accumulator.get_feedback_bias_text()
            transcript_window = "\n".join(list(self._transcript_segments)[-_TRANSCRIPT_WINDOW:])

            hint = await asyncio.to_thread(
                self._cascade.run,
                transcript_window,
                context_summary,
                feedback_bias,
            )

            if await self._send_hint_message(hint):
                self._last_hint_time = time.time()
                return

            await self._send_fallback_hints()
            self._last_hint_time = time.time()
        except Exception as e:
            logger.warning("Hint generation error: %s", e)
            await self._channel.send(
                ErrorMessage(code="hints", message=str(e)).model_dump()
            )

    # ── audio processing ───────────────────────────────────────

    async def _process_audio(self, source: str, audio: bytes) -> None:
        speaker = "user" if source == "mic" else "opponent"
        prefix = "[Вы]:" if speaker == "user" else "[Оппонент]:"

        _asr_start = time.time()
        invalid_before = self._invalid_count()
        try:
            text = await self._adapter.process_chunk_bytes(audio, source)
        except ASRError as e:
            logger.warning("ASR failed after provider retries: %s", e)
            await self._channel.send(
                ErrorMessage(code="asr", message="Ошибка распознавания речи").model_dump()
            )
            self._stats.chunks_processed += 1
            return
        except AudioProcessingError as e:
            logger.warning("Audio processing error: %s", e)
            await self._channel.send(
                ErrorMessage(code="asr", message="Ошибка обработки аудио").model_dump()
            )
            self._stats.chunks_processed += 1
            return

        logger.info("ASR processing time: %.2fs for source=%s", time.time() - _asr_start, source)
        self._stats.chunks_processed += 1

        if not text or not text.strip():
            status = "invalid_chunk" if self._invalid_count() > invalid_before else "silent_chunk"
            await self._channel.send(
                StatusMessage(status=status, **self._status_payload()).model_dump()
            )
            return

        now = time.time()
        clean_text = text.strip()
        segment = f"{prefix} {clean_text}"
        self._transcript_segments.append(segment)

        await self._channel.send(
            TranscriptMessage(
                text=clean_text,
                speaker=speaker,
                timestamp=now,
            ).model_dump()
        )

        self._accumulator.extract_facts_from_text(clean_text, now)
        self._accumulator.update_phase_from_text(clean_text)

        await self._maybe_generate_hints(now, clean_text)

    async def _maybe_generate_hints(self, now: float, clean_text: str) -> None:
        pause_event = self._event_detector.check_pause(now)
        keyword_events = self._event_detector.process_transcript_chunk(clean_text)
        timer_fired = self._event_detector.should_trigger_timer(now)

        trigger_event = None
        if keyword_events:
            trigger_event = keyword_events[0]
        elif pause_event:
            trigger_event = pause_event
        elif timer_fired:
            trigger_event = self._event_detector.create_timer_event(
                "\n".join(list(self._transcript_segments)[-_TRANSCRIPT_WINDOW:])
            )

        cascade_hint_sent = False
        if (
            trigger_event
            and self._event_detector.should_trigger(trigger_event)
            and (now - self._last_hint_time) >= _CASCADE_MIN_INTERVAL
        ):
            self._event_detector.mark_event_processed()
            self._event_detector.reset_timer()

            context_summary = self._accumulator.get_context_summary()
            feedback_bias = self._accumulator.get_feedback_bias_text()
            transcript_window = "\n".join(list(self._transcript_segments)[-_TRANSCRIPT_WINDOW:])

            try:
                hint = await asyncio.to_thread(
                    self._cascade.run,
                    transcript_window,
                    context_summary,
                    feedback_bias,
                )
                cascade_hint_sent = await self._send_hint_message(hint)
                if cascade_hint_sent:
                    self._last_hint_time = now
            except Exception as e:
                logger.warning("Cascade hint generation error: %s", e)

        if (
            not cascade_hint_sent
            and self._session_config
            and now - self._last_hint_time >= _FALLBACK_HINT_INTERVAL
        ):
            self._last_hint_time = now
            try:
                await self._send_fallback_hints()
            except Exception as e:
                logger.warning("Hint generation error: %s", e)
                await self._channel.send(
                    ErrorMessage(code="hints", message=str(e)).model_dump()
                )

    # ── control frames (inline in receive loop) ────────────────

    async def _handle_control_message(self, raw: dict) -> None:
        msg_type = raw.get("type", "")

        if msg_type == "session_config":
            try:
                cfg = SessionConfigMessage(**raw)
                self._session_config = {
                    "template_key": cfg.template_key,
                    "context_text": cfg.context_text,
                }
                await self._channel.send(
                    StatusMessage(status="ready").model_dump()
                )
            except Exception as e:
                await self._channel.send(
                    ErrorMessage(code="invalid_config", message=str(e)).model_dump()
                )

        elif msg_type == "brief_update":
            try:
                brief_msg = BriefUpdateMessage(**raw)
                self._accumulator.update_brief(
                    brief_msg.apply_to(self._accumulator.meeting_brief)
                )
                await self._channel.send(
                    StatusMessage(status="ready").model_dump()
                )
            except Exception as e:
                await self._channel.send(
                    ErrorMessage(code="invalid_brief", message=str(e)).model_dump()
                )

        elif msg_type == "hint_feedback":
            try:
                fb_msg = HintFeedbackMessage(**raw)
                found = self._accumulator.record_feedback(fb_msg.hint_id, fb_msg.rating)
                await self._channel.send(
                    FeedbackAckMessage(
                        hint_id=fb_msg.hint_id,
                        status="recorded" if found else "not_found",
                    ).model_dump()
                )
            except Exception as e:
                await self._channel.send(
                    ErrorMessage(code="invalid_feedback", message=str(e)).model_dump()
                )

        elif msg_type == "hint_request":
            self._enqueue(("hint_request",))

        else:
            await self._channel.send(
                ErrorMessage(
                    code="unknown_type",
                    message=f"Unknown message type: {msg_type}",
                ).model_dump()
            )

    # ── lifecycle ──────────────────────────────────────────────

    async def run(self) -> None:
        dispatcher = asyncio.create_task(self._dispatch(), name="lh-dispatch")
        processor = asyncio.create_task(self._processor(), name="lh-process")

        stream = StreamSession(self._ws)

        try:
            while True:
                kind, *rest = await stream.receive()

                if kind == "audio":
                    source, audio = rest
                    self._enqueue(("audio", source, audio))
                    continue

                (raw,) = rest
                await self._handle_control_message(raw)

        except WsProtocolError as e:
            logger.warning("Live-hints protocol violation: %s (%s)", e.reason, e.code)
        except WebSocketDisconnect:
            logger.info("Live-hints WebSocket disconnected")
        except Exception:
            logger.exception(
                "Live-hints WebSocket error (correlation_id=%s)", get_correlation_id()
            )
            try:
                await self._channel.close_terminal(
                    ErrorMessage(
                        code="server",
                        message="Internal server error",
                        correlation_id=get_correlation_id(),
                    ).model_dump()
                )
            except Exception:
                logger.debug("failed to deliver terminal error event", exc_info=True)
                await close_ws(self._ws, 1011)
        finally:
            dispatcher.cancel()
            processor.cancel()
            await asyncio.gather(dispatcher, processor, return_exceptions=True)
            for q in (self._recv_queue, self._process_queue):
                while True:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            try:
                await self._adapter.close()
            except Exception:
                logger.debug("audio adapter close failed", exc_info=True)
            self._transcript_segments.clear()
            await self._channel.shutdown()
            await close_ws(self._ws, 1000)


@router.websocket("/ws")
async def live_hints_ws(ws: WebSocket):
    async with ws_db_session() as db:
        identity = await authenticate_websocket(ws, db)
    if identity is None:
        return

    channel = OutboundChannel(ws)
    await channel.start()
    await channel.send({"type": "auth_ok", "protocol": WS_PROTOCOL_VERSION})

    provider_preference = await _load_provider_preference(identity.user_id)

    session = _LiveHintsSession(ws, channel, provider_preference)
    await session.run()
