"""Live hints service for real-time conversation assistance.

Provides hint templates and placeholder functions for generating
argumentative and navigational hints during live transcription.
"""

import asyncio
import base64
import json
import logging
import os
import re
import tempfile

from .context_utils import estimate_tokens
from .exceptions import ASRError
from .mistral_client import MistralASRClient
from .summarizer import LLMClient

logger = logging.getLogger(__name__)

# --- Constants ---

CONTEXT_WINDOW_TOKENS = 3000  # ~3 minutes of speech
HINT_CATEGORIES = ["argumentative", "navigational"]
HINT_GENERATION_INTERVAL = 10  # seconds between hint generations

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
    """Per-session audio processing adapter: WebM → ASR transcription."""

    def __init__(self) -> None:
        asr_url = os.getenv("ASR_URL", "https://api.mistral.ai/v1/asr")
        model = os.getenv("ASR_MODEL", "voxtral-mini-latest")
        api_key = os.getenv("MISTRAL_API_KEY", "")
        proxy = os.getenv("ASR_PROXY") or os.getenv("HTTPS_PROXY")
        self._asr_client = MistralASRClient(
            asr_url=asr_url, model=model, api_key=api_key, proxy=proxy
        )

    async def process_chunk(self, audio_b64: str, source: str) -> str:
        """Decode base64 WebM audio, send directly to Mistral ASR.

        Args:
            audio_b64: Base64-encoded WebM audio data.
            source: Identifier for the audio source (e.g. participant name).

        Returns:
            Transcribed text.

        Raises:
            ASRError: If transcription via ASR service fails.
        """
        raw_bytes = base64.b64decode(audio_b64)
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".webm")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
            
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None, self._asr_client.transcribe_raw, tmp_path
            )
            return text
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError(
                f"Transcription failed for source '{source}'", cause=exc
            ) from exc
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def close(self) -> None:
        """Release ASR client resources."""
        if hasattr(self._asr_client, "close"):
            self._asr_client.close()


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
    full_text = truncate_to_window(full_text)

    arg_response = llm_client.call(template["argumentative_prompt"], full_text, max_tokens=1000)
    nav_response = llm_client.call(template["navigational_prompt"], full_text, max_tokens=1000)

    arg_hints = parse_hints_response(arg_response)
    nav_hints = parse_hints_response(nav_response)

    for hint in arg_hints:
        hint["hint_type"] = "argumentative"
    for hint in nav_hints:
        hint["hint_type"] = "navigational"

    return arg_hints + nav_hints


def _normalize_hint(item: dict[str, object]) -> dict[str, str]:
    """Normalize a parsed hint dict to have hint_type, text, and priority keys."""
    hint: dict[str, str] = {
        "hint_type": str(item.get("hint_type") or item.get("type", "argumentative")),
        "text": str(item.get("text", "")),
        "priority": str(item.get("priority", "medium")),
    }
    return hint


def truncate_to_window(text: str, max_tokens: int = CONTEXT_WINDOW_TOKENS) -> str:
    """Truncate transcript to fit within max_tokens, keeping the most recent text.

    Uses a sliding window approach: if the text exceeds the token limit,
    words are dropped from the beginning until the remaining text fits.

    Args:
        text: Full transcript text.
        max_tokens: Maximum allowed token count.

    Returns:
        Truncated text that fits within the token budget.
    """
    if estimate_tokens(text) <= max_tokens:
        return text

    words = text.split()
    kept: list[str] = []
    for word in reversed(words):
        kept.append(word)
        if estimate_tokens(" ".join(reversed(kept))) > max_tokens:
            kept.pop()
            break

    result = " ".join(reversed(kept))
    logger.debug("Truncated transcript from %d to %d tokens", estimate_tokens(text), estimate_tokens(result))
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
