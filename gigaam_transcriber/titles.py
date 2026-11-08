"""
LLM-генерация заголовков сохранённых транскрипций.

Заголовок отражает суть диалога; дата и длительность и так видны в
списке расшифровок. Модель — по умолчанию GigaChat 3.1 10B через
LiteLLM-шлюз (имя должно совпадать со списком ``/v1/models`` шлюза).
Любая ошибка или таймаут => ``None``, вызывающий код откатывается на
дефолтный заголовок (дата/имя файла) — сохранение никогда не ломается.
"""

import asyncio
import logging
import os
from typing import Optional

from gigaam_transcriber.summarizer import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_TITLE_MODEL = os.getenv("LLM_TITLE_MODEL", "ai-sage/GigaChat3.1-10B-A1.8B")

TITLE_MAX_INPUT_CHARS = 6000
TITLE_MAX_LEN = 120
TITLE_MAX_TOKENS = 48
TITLE_TIMEOUT_SECONDS = 25.0

TITLE_SYSTEM_PROMPT = (
    "Ты генерируешь короткие заголовки для расшифровок аудиозаписей. "
    "Прочитай транскрипцию и сформулируй её суть одним заголовком на русском: "
    "3-8 слов — тема разговора и ключевой предмет или решение. "
    "Без кавычек, без точки в конце, без префиксов вроде «Транскрипция», "
    "«Запись», «Диалог». Ответь ТОЛЬКО заголовком."
)


def _clean_title(raw: Optional[str]) -> Optional[str]:
    """Нормализует ответ модели: одна строка, без кавычек, ограничение длины."""
    if not raw:
        return None
    title = " ".join(raw.split())
    title = title.strip().strip("\"'«»‘’“” .,;:").strip()
    if not title:
        return None
    return title[:TITLE_MAX_LEN].rstrip(" .,;:")


def generate_title_sync(full_text: str, client: Optional[LLMClient] = None) -> Optional[str]:
    """Синхронная генерация заголовка; ``None`` — LLM недоступен или ответ пуст."""
    text = (full_text or "").strip()
    if not text:
        return None
    try:
        llm = client or LLMClient()
        raw = llm.call(
            TITLE_SYSTEM_PROMPT,
            text[:TITLE_MAX_INPUT_CHARS],
            max_tokens=TITLE_MAX_TOKENS,
            model_override=DEFAULT_TITLE_MODEL,
        )
    except Exception:
        logger.warning("LLM title generation failed", exc_info=True)
        return None
    return _clean_title(raw)


async def generate_title(full_text: str) -> Optional[str]:
    """Асинхронная обёртка: blocking-вызов LLM уходит в поток, общий таймаут."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(generate_title_sync, full_text),
            timeout=TITLE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "LLM title generation timed out after %.0fs", TITLE_TIMEOUT_SECONDS
        )
        return None
    except Exception:
        logger.warning("LLM title generation error", exc_info=True)
        return None
