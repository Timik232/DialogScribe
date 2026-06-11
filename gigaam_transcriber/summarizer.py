"""
LLM-суммаризация транскрипций с поддержкой шаблонов.

Предоставляет LLMClient для работы с OpenAI-совместимыми API,
шаблоны промптов и генерацию саммари с map-reduce для длинных текстов.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import markdown as md_lib
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, AuthenticationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.context_utils import estimate_tokens, get_model_context_limit
from gigaam_transcriber.template_manager import TemplateManager

logger = logging.getLogger("gigaam_transcriber.llm")

# ---------------------------------------------------------------------------
# Конфигурация по умолчанию
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1"
MAX_CHUNK_TOKENS = 3000
CHUNK_OVERLAP_SENTENCES = 2


def _default_base_url() -> str:
    return os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)


def _default_model() -> str:
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def parse_models_csv(models_csv: str | None) -> list[str]:
    if not models_csv:
        return []
    models = [m.strip() for m in models_csv.split(",")]
    return [m for m in models if m]


def get_available_models() -> list[str]:
    models_csv = os.getenv("LLM_MODELS", "")
    models = parse_models_csv(models_csv)
    if not models:
        models = [_default_model()]
    return list(dict.fromkeys(models))


def _default_api_key() -> str:
    return os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))


# ---------------------------------------------------------------------------
# Шаблоны суммаризации
# ---------------------------------------------------------------------------

SUMMARY_TEMPLATES: dict[str, dict[str, str]] = {
    "meeting": {
        "label": "📝 Встреча",
        "system_prompt": (
            "Ты — профессиональный секретарь meetings. Проанализируй транскрипцию встречи "
            "и составь структурированное саммари на русском языке.\n\n"
            "Структура ответа:\n"
            "## 📋 Основная информация\n"
            "- **Тема встречи**\n"
            "- **Участники** (перечисли всех спикеров)\n"
            "- **Дата/длительность** (если есть)\n\n"
            "## 🎯 Ключевые решения\n"
            "1. ...\n\n"
            "## ✅ Action Items\n"
            "| Задача | Ответственный | Срок |\n"
            "|--------|---------------|------|\n"
            "| ...    | ...           | ...  |\n\n"
            "## 💬 Важные цитаты\n"
            "> «цитата» — Спикер\n\n"
            "## 📌 Итоги и выводы\n"
            "- ...\n\n"
            "Формат: Markdown. Будь точным, не придумывай факты."
        ),
    },
    "lecture": {
        "label": "🎓 Лекция",
        "system_prompt": (
            "Ты — академический ассистент. Проанализируй транскрипцию лекции/презентации "
            "и составь структурированный конспект на русском языке.\n\n"
            "Структура ответа:\n"
            "## 📚 Тема лекции\n"
            "Название/описание\n\n"
            "## 🗂️ Основные темы\n"
            "### Тема 1\n"
            "- Ключевые тезисы\n"
            "- Определения терминов\n\n"
            "### Тема 2\n"
            "- ...\n\n"
            "## 🔑 Ключевые термины\n"
            "| Термин | Определение |\n"
            "|--------|-------------|\n"
            "| ...    | ...         |\n\n"
            "## 💡 Главные выводы\n"
            "1. ...\n\n"
            "## ❓ Вопросы для самопроверки\n"
            "1. ...\n\n"
            "Формат: Markdown. Структурируй логически, выделяй термины."
        ),
    },
    "interview": {
        "label": "🎤 Интервью",
        "system_prompt": (
            "Ты — редактор-аналитик. Проанализируй транскрипцию интервью/диалога "
            "и составь структурированное саммари на русском языке.\n\n"
            "Структура ответа:\n"
            "## 👤 Участники\n"
            "- **Интервьюер**: ...\n"
            "- **Собеседник**: ...\n\n"
            "## 📝 Обзор беседы\n"
            "Краткое описание хода интервью\n\n"
            "## ❓ Вопросы и ответы\n"
            "### Вопрос 1: <текст вопроса>\n"
            "**Ответ**: <ключевые моменты ответа>\n\n"
            "### Вопрос 2: ...\n\n"
            "## 🏷️ Основные темы\n"
            "1. ...\n\n"
            "## 💬 Яркие цитаты\n"
            "> «цитата» — Спикер\n\n"
            "## 📌 Итоги\n"
            "- ...\n\n"
            "Формат: Markdown. Сохраняй точность цитат."
        ),
    },
    "general": {
        "label": "📄 Общий",
        "system_prompt": (
            "Ты — аналитик текстов. Проанализируй транскрипцию и составь "
            "структурированное саммари на русском языке.\n\n"
            "Структура ответа:\n"
            "## 📋 Обзор\n"
            "Краткое описание содержания\n\n"
            "## 🔑 Ключевые моменты\n"
            "1. ...\n\n"
            "## 📊 Основные данные\n"
            "- Числа, факты, имена\n\n"
            "## 💡 Выводы\n"
            "- ...\n\n"
            "Формат: Markdown. Будь точным и объективным."
        ),
    },
    "sales": {
        "label": "💼 Продажи",
        "system_prompt": (
            "Ты — аналитик B2B-продаж. Проанализируй транскрипцию звонка менеджера с клиентом "
            "и составь структурированный отчёт на русском языке.\n\n"
            "СТРОГО используй эти заголовки второго уровня (##):\n\n"
            "## Резюме\n"
            "2-4 предложения: что обсуждали, чем закончили, общий тон встречи.\n\n"
            "## Следующие шаги\n"
            "Конкретные задачи. Каждый пункт строго в формате:\n"
            "- **[Ответственный]** Задача · Приоритет: высокий/средний/низкий\n"
            "Если указан срок, добавь его после задачи через · _до ДД.ММ_\n\n"
            "## Возражения\n"
            "Каждое возражение клиента и реакция менеджера:\n"
            "- **Возражение**: текст возражения → **Ответ**: как менеджер ответил (или «не отработано»)\n"
            "Если возражений не было — напиши «Возражений не выявлено».\n\n"
            "## Сигналы клиента\n"
            "Разбей на категории:\n"
            "- **Интерес**: к чему клиент проявил интерес\n"
            "- **Боль**: какие проблемы или потребности озвучил\n"
            "- **Готовность**: покупательские сигналы (запрос цены, сроков, деталей)\n"
            "- **Сомнения**: что вызвало настороженность\n\n"
            "## Оценка сделки\n"
            "- **Этап воронки**: квалификация / выявление потребностей / презентация / "
            "работа с возражениями / согласование условий / закрытие\n"
            "- **Вероятность закрытия**: X% — коротко почему\n"
            "- **Риски**: что может помешать сделке\n"
            "- **Рекомендация**: 1-2 предложения что делать дальше\n\n"
            "Правила:\n"
            "- Будь конкретен, ссылайся на слова из транскрипта\n"
            "- Не выдумывай то, чего нет в разговоре\n"
            "- Если данных для раздела нет — напиши «Не выявлено»\n"
            "- Формат: Markdown"
        ),
    },
}


# ---------------------------------------------------------------------------
# LLM-клиент
# ---------------------------------------------------------------------------


@dataclass
class LLMClientConfig:
    """Конфигурация LLM-клиента."""

    base_url: str = field(default_factory=_default_base_url)
    api_key: str = field(default_factory=_default_api_key)
    model: str = field(default_factory=_default_model)
    max_retries: int = 3


class LLMClient:
    """Клиент для OpenAI-совместимого LLM API."""

    def __init__(self, config: Optional[LLMClientConfig] = None):
        self._config = config or LLMClientConfig()
        self._max_retries = self._config.max_retries
        self._client: Optional[OpenAI] = None

    @property
    def config(self) -> LLMClientConfig:
        return self._config

    def _get_client(self) -> OpenAI:
        """Ленивая инициализация OpenAI-клиента."""
        if self._client is None:
            if not self._config.api_key:
                raise ValueError(
                    "LLM API key не задан в окружении. "
                    "Укажите LLM_API_KEY (или OPENAI_API_KEY) через Vault/.env."
                )
            self._client = OpenAI(
                base_url=self._config.base_url,
                api_key=self._config.api_key,
            )
        return self._client

    def update_config(self, base_url: str, api_key: str, model: str) -> None:
        """Обновить конфигурацию и пересоздать клиент."""
        self._config.base_url = base_url or DEFAULT_BASE_URL
        self._config.api_key = api_key
        self._config.model = model or DEFAULT_MODEL
        self._client = None

    def call(self, system_prompt: str, user_text: str, max_tokens: int = 4096) -> str:
        """
        Вызов LLM API с обработкой ошибок и retry.

        Args:
            system_prompt: Системный промпт
            user_text: Текст пользователя (транскрипция)
            max_tokens: Максимум токенов в ответе

        Returns:
            Текстовый ответ от LLM

        Raises:
            ValueError: API key не задан
            ConnectionError: Ошибка подключения
            RuntimeError: Ошибка API
        """
        client = self._get_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

        logger.info(
            "LLM call starting | model=%s | estimated_input_tokens=%d | messages=%d",
            self._config.model,
            estimate_tokens(system_prompt + user_text),
            len(messages),
        )

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            t0 = time.time()
            try:
                response = client.chat.completions.create(
                    model=self._config.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
                latency_ms = (time.time() - t0) * 1000
                logger.info(
                    "LLM call completed | model=%s | input_tokens=%d | output_tokens=%d | latency_ms=%.0f | status=success",
                    self._config.model,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    latency_ms,
                )
                return response.choices[0].message.content or ""

            except AuthenticationError as e:
                latency_ms = (time.time() - t0) * 1000
                logger.error(
                    "LLM call failed | model=%s | error_type=%s | latency_ms=%.0f | status=error",
                    self._config.model,
                    type(e).__name__,
                    latency_ms,
                )
                raise ValueError(f"Ошибка авторизации: {e}") from e

            except (RateLimitError, APIConnectionError) as e:
                last_exc = e
                if attempt < self._max_retries:
                    backoff = float(2 ** attempt)
                    logger.warning(
                        "LLM call retry | model=%s | attempt=%d/%d | error_type=%s | backoff=%.1fs",
                        self._config.model,
                        attempt + 1,
                        self._max_retries,
                        type(e).__name__,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                latency_ms = (time.time() - t0) * 1000
                logger.error(
                    "LLM call failed | model=%s | error_type=%s | latency_ms=%.0f | status=error",
                    self._config.model,
                    type(e).__name__,
                    latency_ms,
                )

            except APIError as e:
                status = getattr(e, "status_code", None)
                if status is not None and status >= 500 and attempt < self._max_retries:
                    last_exc = e
                    backoff = float(2 ** attempt)
                    logger.warning(
                        "LLM call retry | model=%s | attempt=%d/%d | error_type=%s | backoff=%.1fs",
                        self._config.model,
                        attempt + 1,
                        self._max_retries,
                        type(e).__name__,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                latency_ms = (time.time() - t0) * 1000
                logger.error(
                    "LLM call failed | model=%s | error_type=%s | latency_ms=%.0f | status=error",
                    self._config.model,
                    type(e).__name__,
                    latency_ms,
                )
                if status is not None and status >= 500:
                    last_exc = e
                    continue
                raise RuntimeError(f"Ошибка API: {e}") from e

        if last_exc is None:
            raise RuntimeError("LLM call failed unexpectedly")
        if isinstance(last_exc, RateLimitError):
            raise RuntimeError(f"Превышен лимит запросов: {last_exc}") from last_exc
        if isinstance(last_exc, APIConnectionError):
            raise ConnectionError(f"Не удалось подключиться к {self._config.base_url}: {last_exc}") from last_exc
        raise RuntimeError(f"Ошибка API: {last_exc}") from last_exc

    def test_connection(self) -> tuple[bool, str]:
        """
        Проверить подключение к LLM API.

        Returns:
            Кортеж (success, message)
        """
        if not self._config.api_key:
            return False, "API key не задан"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            if response.choices:
                return True, f"Подключение успешно (модель: {self._config.model})"
            return False, "Пустой ответ от API"
        except AuthenticationError:
            return False, "Неверный API key"
        except APIConnectionError:
            return False, f"Не удалось подключиться к {self._config.base_url}"
        except APIError as e:
            return False, f"Ошибка API: {e}"
        except Exception as e:
            return False, f"Неизвестная ошибка: {e}"


# ---------------------------------------------------------------------------
# Разделение текста на части
# ---------------------------------------------------------------------------


def split_text(
    text: str, max_tokens: int = MAX_CHUNK_TOKENS, overlap_sentences: int = CHUNK_OVERLAP_SENTENCES
) -> list[str]:
    if estimate_tokens(text) <= max_tokens:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        sentences = text.split("\n")
    if len(sentences) <= 1:
        words = text.split()
        chunk_size = max_tokens * 2
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i : i + chunk_size]))
        return chunks if chunks else [text]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = estimate_tokens(sentence)
        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = current_chunk[-overlap_sentences:] if overlap_sentences > 0 else []
            current_tokens = sum(estimate_tokens(s) for s in current_chunk)

        current_chunk.append(sentence)
        current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Генерация саммари
# ---------------------------------------------------------------------------


async def generate_summary(
    transcription_text: str,
    template_key: str,
    llm_client: LLMClient,
    db: Optional["AsyncSession"] = None,
    user_id: Optional[str] = None,
) -> str:
    """
    Сгенерировать саммари транскрипции.

    Args:
        transcription_text: Полный текст транскрипции
        template_key: Ключ шаблона (meeting, lecture, interview, general)
        llm_client: Настроенный LLM-клиент
        db: AsyncSession для доступа к БД шаблонов
        user_id: ID пользователя для загрузки кастомных шаблонов

    Returns:
        Markdown-строка с саммари
    """
    if db and user_id:
        all_templates = await TemplateManager.get_all_templates(db, user_id, SUMMARY_TEMPLATES)
    else:
        all_templates = SUMMARY_TEMPLATES
    template = all_templates.get(template_key)
    if not template:
        raise ValueError(
            f"Неизвестный шаблон: {template_key}. Доступные: {list(all_templates.keys())}"
        )

    system_prompt = template["system_prompt"]
    chunks = split_text(transcription_text)

    if len(chunks) == 1:
        return llm_client.call(system_prompt, chunks[0])

    # Map-reduce для длинных транскрипций
    logger.info("Map-reduce: %d chunks", len(chunks))

    chunk_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug("Summarizing chunk %d/%d", i + 1, len(chunks))
        chunk_prompt = (
            f"{system_prompt}\n\n"
            f"⚠️ Это часть {i + 1} из {len(chunks)} полной транскрипции. "
            f"Суммаризируй эту часть, сохранив все ключевые факты."
        )
        summary = llm_client.call(chunk_prompt, chunk)
        chunk_summaries.append(summary)

    combined = "\n\n---\n\n".join(chunk_summaries)
    reduce_prompt = (
        "Ты — редактор. Перед тобой частичные саммари одной транскрипции, "
        "разбитой на части. Объедини их в единое целостное саммари, "
        "убрав дубли и сохранив структуру.\n\n"
        f"Используй формат:\n{system_prompt}"
    )
    final = llm_client.call(reduce_prompt, combined)
    return final


def summary_to_html(markdown_text: str) -> str:
    """Конвертировать Markdown-саммари в HTML для Gradio."""
    return md_lib.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )
