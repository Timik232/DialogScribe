"""Live hints service for real-time conversation assistance.

Provides hint templates and placeholder functions for generating
argumentative and navigational hints during live transcription.
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .audio_processor import _run_subprocess
from .context_utils import estimate_tokens, estimate_tokens_accurate
from .exceptions import ASRError
from .asr_provider import get_asr_provider
from .summarizer import LLMClient

logger = logging.getLogger(__name__)

# --- Constants ---

CONTEXT_WINDOW_TOKENS = 3000  # ~3 minutes of speech
HINT_CATEGORIES = ["argumentative", "navigational"]
HINT_GENERATION_INTERVAL = 30  # seconds between hint generations


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


LIVE_HINTS_FFMPEG_TIMEOUT_SECONDS = _env_float(
    "LIVE_HINTS_FFMPEG_TIMEOUT_SECONDS", 10.0
)


def _live_hints_ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


@dataclass(frozen=True)
class LiveHintsBounds:
    """Env-tunable bounds for one live-hints session (queues, caps, policy).

    Read once per session; every queue and accumulator the session owns is
    bounded by one of these values so memory cannot grow with session time.
    """

    recv_queue: int = 256
    process_queue: int = 64
    max_segments: int = 200
    asr_attempts: int = 2
    llm_attempts: int = 2
    max_chunk_seconds: float = 30.0
    status_interval: float = 5.0

    @classmethod
    def from_env(cls) -> "LiveHintsBounds":
        return cls(
            recv_queue=_env_int("LIVE_HINTS_RECV_QUEUE", 256),
            process_queue=_env_int("LIVE_HINTS_PROCESS_QUEUE", 64),
            max_segments=_env_int("LIVE_HINTS_MAX_SEGMENTS", 200),
            asr_attempts=_env_int("LIVE_HINTS_ASR_ATTEMPTS", 2),
            llm_attempts=_env_int("LIVE_HINTS_LLM_ATTEMPTS", 2),
            max_chunk_seconds=_env_float("LIVE_HINTS_MAX_CHUNK_SECONDS", 30.0),
            status_interval=_env_float("LIVE_HINTS_STATUS_INTERVAL", 5.0),
        )

# --- Hint Templates ---

HINT_TEMPLATES: dict[str, dict[str, str]] = {
    "negotiation": {
        "label": "🤝 Переговоры",
        "argumentative_prompt": (
            "Ты — эксперт по переговорам. Проанализируй текущую транскрипцию переговоров "
            "и предложи контраргументы, подтверждающие факты и уточняющие вопросы, "
            "которые помогут укрепить позицию собеседника.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "argumentative", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Контраргументы к позициям оппонента\n"
            "- Факты и цифры для подкрепления позиции\n"
            "- Уточняющие вопросы для прояснения условий\n"
            "- Выявленные слабые места в аргументации оппонента\n\n"
            "Приоритет high — если вопрос критичный или сделка под угрозой.\n"
            "Будь кратким, конкретным, без воды."
        ),
        "navigational_prompt": (
            "Ты — модератор переговоров. Проанализируй текущий ход переговоров "
            "и предложи темы для перехода, ключевые вопросы и направления развития "
            "беседы, которые помогут продвинуть переговоры к конструктивному результату.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "navigational", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Предложения по переходу к нерешённым темам\n"
            "- Важные вопросы, которые стоит задать\n"
            "- Напоминания о невысказанных позициях\n"
            "- Предложения по суммированию прогресса\n\n"
            "Приоритет high — если переговоры заходят в тупик или упущена ключевая тема.\n"
            "Будь кратким, конкретным, без воды."
        ),
    },
    "sales": {
        "label": "💰 Продажи",
        "argumentative_prompt": (
            "Ты — эксперт по продажам. Проанализируй транскрипцию разговора с клиентом "
            "и предложи аргументы для отработки возражений, ключевые ценностные "
            "предложения и факты, которые помогут закрыть сделку.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "argumentative", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Отработка возражений клиента с фактами\n"
            "- Ценностные предложения под выявленные потребности\n"
            "- Кейсы и примеры успешных клиентов\n"
            "- Данные о ROI и экономической выгоде\n\n"
            "Приоритет high — если клиент готов уйти или выразил серьёзное возражение.\n"
            "Будь кратким, конкретным, без воды."
        ),
        "navigational_prompt": (
            "Ты — тренер по продажам. Проанализируй текущий этап воронки продаж "
            "в разговоре с клиентом и предложи следующие шаги, техники закрытия "
            "и способы выстраивания раппорта.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "navigational", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Определение текущего этапа воронки и следующий шаг\n"
            "- Техники закрытия сделки подходящие к моменту\n"
            "- Вопросы для выявления скрытых потребностей\n"
            "- Способы укрепления доверия и раппорта\n\n"
            "Приоритет high — если клиент показал покупательский сигнал или готов к закрытию.\n"
            "Будь кратким, конкретным, без воды."
        ),
    },
    "discussion": {
        "label": "💬 Обсуждение",
        "argumentative_prompt": (
            "Ты — аналитик дискуссий. Проанализируй текущую транскрипцию обсуждения "
            "и предложи факты, аргументы за и против, а также уточняющие вопросы "
            "для углубления и конструктивного развития дискуссии.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "argumentative", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Факты и данные по обсуждаемой теме\n"
            "- Аргументы за и против высказанных позиций\n"
            "- Логические нестыковки в высказываниях\n"
            "- Уточняющие вопросы для прояснения позиций\n\n"
            "Приоритет high — если обнаружена существенная ошибка в фактах или логике.\n"
            "Будь кратким, конкретным, без воды."
        ),
        "navigational_prompt": (
            "Ты — модератор обсуждений. Проанализируй текущий ход дискуссии "
            "и предложи переходы между темами, открытые вопросы и моменты "
            "для подведения промежуточных итогов.\n\n"
            "Формат ответа — JSON-массив:\n"
            '[{"hint_type": "navigational", "text": "текст подсказки", '
            '"priority": "high"|"medium"|"low"}]\n\n'
            "Типы подсказок:\n"
            "- Предложения по переходу к нерассмотренным аспектам темы\n"
            "- Открытые вопросы для вовлечения молчащих участников\n"
            "- Моменты для подведения промежуточных итогов\n"
            "- Связывание текущей темы с ранее обсуждёнными\n\n"
            "Приоритет high — если дискуссия зациклилась или участникам нужно подвести итог.\n"
            "Будь кратким, конкретным, без воды."
        ),
    },
}


# --- Placeholder Classes & Functions ---

class AudioAdapter:
    """Per-session audio processing adapter: WebM → ASR transcription.

    The ASR provider chain is constructed with the session retry policy
    (``LIVE_HINTS_ASR_ATTEMPTS``, default 2 total attempts) so retries have
    exactly ONE owner — the provider client. The adapter itself never retries.
    """

    def __init__(self, provider_preference: str | None = None) -> None:
        attempts = _env_int("LIVE_HINTS_ASR_ATTEMPTS", 2)
        self.max_chunk_seconds = _env_float("LIVE_HINTS_MAX_CHUNK_SECONDS", 30.0)
        self.invalid_chunks = 0
        self._last_invalid_log = 0.0
        self._asr_client = get_asr_provider(
            preference=provider_preference,
            max_retries=max(0, attempts - 1),
        )

    async def process_chunk(self, audio_b64: str, source: str) -> str:
        """Decode base64 WebM audio and delegate to :meth:`process_chunk_bytes`."""
        return await self.process_chunk_bytes(base64.b64decode(audio_b64), source)

    async def process_chunk_bytes(self, raw_bytes: bytes, source: str) -> str:
        """Convert raw WebM audio to WAV via ffmpeg and send to ASR.

        MediaRecorder with timeslice produces partial WebM fragments that lack
        proper container headers, causing Mistral to reject them.  Converting to
        WAV via ffmpeg produces a valid, self-contained audio file.

        The ffmpeg subprocess and file IO run in a worker thread
        (:func:`asyncio.to_thread`) so the event loop is never blocked.

        Args:
            raw_bytes: Raw WebM audio bytes.
            source: Identifier for the audio source (e.g. participant name).

        Returns:
            Transcribed text.

        Raises:
            ASRError: If transcription via ASR service fails.
        """
        try:
            wav_bytes = await asyncio.to_thread(self._decode_to_wav, raw_bytes)
            if wav_bytes is None:
                return ""
            verdict = self._check_wav(wav_bytes)
            if verdict == "invalid":
                self.invalid_chunks += 1
                now = time.monotonic()
                if now - self._last_invalid_log >= 5.0:
                    self._last_invalid_log = now
                    logger.warning(
                        "live-hints chunk rejected: duration/size sanity failed "
                        "(bytes=%d, cap=%.0fs), total invalid=%d",
                        len(wav_bytes), self.max_chunk_seconds, self.invalid_chunks,
                    )
                return ""
            if verdict == "silent":
                return ""
            transcription = await self._asr_client.transcribe_raw(
                wav_bytes, "audio.wav"
            )
            return transcription if transcription else ""
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError(
                f"Transcription failed for source '{source}'", cause=exc
            ) from exc

    def _check_wav(self, wav_bytes: bytes) -> str:
        """Sanity-check decoded WAV bytes: "ok", "silent" (no frames) or "invalid".

        Rejects absurd durations, oversized payloads and non-audio garbage
        without raising, so a bad chunk never crashes the processor.
        """
        max_bytes = int(self.max_chunk_seconds * 32000 * 2) + 4096
        if not wav_bytes or len(wav_bytes) > max_bytes:
            return "invalid"
        try:
            with wave.open(io.BytesIO(wav_bytes)) as w:
                frames = w.getnframes()
                rate = w.getframerate()
        except Exception:
            return "invalid"
        if rate <= 0 or frames / rate > self.max_chunk_seconds:
            return "invalid"
        if frames == 0:
            return "silent"
        return "ok"

    def _decode_to_wav(self, raw_bytes: bytes) -> bytes | None:
        """Sync worker-thread body: ffmpeg WebM → WAV bytes.

        Returns None when ffmpeg cannot decode the chunk (silence / garbage);
        tempfiles are always removed. ffmpeg runs through the guarded
        subprocess helper (timeout + process-group kill + bounded capture).
        """
        tmp_webm: str | None = None
        tmp_wav: str | None = None
        try:
            fd, tmp_webm = tempfile.mkstemp(suffix=".webm")
            os.close(fd)
            with open(tmp_webm, "wb") as f:
                f.write(raw_bytes)

            fd2, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd2)
            result = _run_subprocess(
                [
                    _live_hints_ffmpeg_path(),
                    "-y",
                    "-i",
                    tmp_webm,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    tmp_wav,
                ],
                timeout=LIVE_HINTS_FFMPEG_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                # ffmpeg couldn't decode — likely silence / empty chunk, skip
                logger.debug("ffmpeg skipped chunk (rc=%d): %s", result.returncode, result.stderr.decode(errors="replace")[:200])
                return None

            with open(tmp_wav, "rb") as f:
                return f.read()
        finally:
            for p in (tmp_webm, tmp_wav):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        logger.warning("live-hints: failed to remove decode tempfile %s", Path(p).name)
                        pass

    async def close(self) -> None:
        await self._asr_client.close()


def generate_hints(
    transcript: str,
    template_key: str,
    llm_client: LLMClient,
    context_text: str = "",
) -> list[dict[str, str]]:
    """Generate argumentative and navigational hints for a live conversation.

    Args:
        transcript: Current transcript text.
        template_key: Key into HINT_TEMPLATES (e.g. "negotiation").
        llm_client: LLMClient instance for calling the LLM.
        context_text: Optional prior context to prepend.

    Returns:
        Combined list of argumentative and navigational hint dicts.
    """
    template = HINT_TEMPLATES[template_key]

    full_text = f"{context_text}\n\n{transcript}" if context_text else transcript
    full_text = truncate_to_window(full_text, model=llm_client.config.model)

    arg_response = llm_client.call(template["argumentative_prompt"], full_text, max_tokens=1000)
    nav_response = llm_client.call(template["navigational_prompt"], full_text, max_tokens=1000)

    arg_hints = parse_hints_response(arg_response)
    nav_hints = parse_hints_response(nav_response)

    for hint in arg_hints:
        hint["hint_type"] = "argumentative"
    for hint in nav_hints:
        hint["hint_type"] = "navigational"

    # Limit total hints: max 1 argumentative + 1 navigational
    return arg_hints[:1] + nav_hints[:1]


def _normalize_hint(item: dict[str, object]) -> dict[str, str]:
    """Normalize a parsed hint dict to have hint_type, text, and priority keys."""
    hint: dict[str, str] = {
        "hint_type": str(item.get("hint_type") or item.get("type", "argumentative")),
        "text": str(item.get("text", "")),
        "priority": str(item.get("priority", "medium")),
    }
    return hint


def truncate_to_window(text: str, max_tokens: int = CONTEXT_WINDOW_TOKENS, model: Optional[str] = None) -> str:
    """Truncate transcript to fit within max_tokens, keeping the most recent text.

    Uses a sliding window approach: if the text exceeds the token limit,
    words are dropped from the beginning until the remaining text fits.

    Args:
        text: Full transcript text.
        max_tokens: Maximum allowed token count.
        model: Optional model name for accurate token estimation.

    Returns:
        Truncated text that fits within the token budget.
    """
    _estimate = lambda t: estimate_tokens_accurate(t, model) if model else estimate_tokens(t)

    if _estimate(text) <= max_tokens:
        return text

    words = text.split()
    kept: list[str] = []
    for word in reversed(words):
        kept.append(word)
        if _estimate(" ".join(reversed(kept))) > max_tokens:
            kept.pop()
            break

    result = " ".join(reversed(kept))
    logger.debug("Truncated transcript from %d to %d tokens", _estimate(text), _estimate(result))
    return result


def parse_hints_response(llm_response: str) -> list[dict[str, str]]:
    """Parse LLM response into a list of hint dicts.

    Attempts, in order:
    1. Direct JSON parse of the response
    2. Extract JSON from markdown code blocks (```json ... ```)
    3. Fallback: treat each non-empty line as a hint

    Returns:
        List of dicts with keys: hint_type, text, priority.
        Empty list if nothing could be parsed.
    """
    try:
        data = json.loads(llm_response)
        if isinstance(data, list):
            return [_normalize_hint(item) for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, ValueError):
        pass

    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", llm_response, re.DOTALL)
    if code_block_match:
        try:
            data = json.loads(code_block_match.group(1).strip())
            if isinstance(data, list):
                return [_normalize_hint(item) for item in data if isinstance(item, dict)]
        except (json.JSONDecodeError, ValueError):
            pass

    lines = [line.strip() for line in llm_response.splitlines() if line.strip()]
    if lines:
        return [{"hint_type": "argumentative", "text": line, "priority": "medium"} for line in lines]

    return []
