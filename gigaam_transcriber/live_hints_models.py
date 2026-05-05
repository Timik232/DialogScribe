"""
Pydantic-модели для WebSocket-сообщений и REST-полезной нагрузки
функции «Живые подсказки» (Live Hints).

Модели описывают формат обмена между клиентом и сервером:
- Клиент → Сервер: AudioChunkMessage, SessionConfigMessage, HintFeedbackMessage
- Сервер → Клиент: TranscriptMessage, HintMessage, ErrorMessage, StatusMessage, FeedbackAckMessage
- REST API: HintTemplate
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


# ─── Клиент → Сервер ───────────────────────────────────────────


class AudioChunkMessage(BaseModel):
    """Аудиочанк, отправляемый клиентом через WebSocket.

    Поля:
        audio_b64: Base64-кодированные аудиоданные (PCM/WAV).
        source: Источник аудио — «mic» (микрофон) или «tab» (вкладка браузера).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_chunk"] = "audio_chunk"
    audio_b64: str
    source: Literal["mic", "tab"]


class SessionConfigMessage(BaseModel):
    """Конфигурация сессии, отправляемая клиентом при старте.

    Поля:
        template_key: Ключ шаблона подсказок.
        context_text: Дополнительный контекст сессии (необязательный).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["session_config"] = "session_config"
    template_key: str
    context_text: str = ""


# ─── Клиент → Сервер (Live Advisor) ─────────────────────────────


class HintFeedbackMessage(BaseModel):
    """Feedback от пользователя на подсказку (лайк/дизлайк)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["hint_feedback"] = "hint_feedback"
    hint_id: str
    rating: Literal["like", "dislike"]


# ─── Сервер → Клиент ───────────────────────────────────────────


class TranscriptMessage(BaseModel):
    """Распознанный фрагмент речи, отправляемый сервером клиенту.

    Поля:
        text: Распознанный текст.
        speaker: Кто говорит — «user» (пользователь) или «opponent» (собеседник).
        timestamp: Unix-время получения фрагмента.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["transcript"] = "transcript"
    text: str
    speaker: Literal["user", "opponent"]
    timestamp: float


class HintMessage(BaseModel):
    """Подсказка, сгенерированная сервером для пользователя.

    Поля:
        hint_type: Тип подсказки — «argumentative» или «navigational».
        text: Текст подсказки.
        priority: Приоритет — «high», «medium» или «low».
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["hint"] = "hint"
    hint_type: Literal["argumentative", "navigational", "tactical", "strategic", "warning", "analytical"]
    text: str
    priority: Literal["high", "medium", "low"]
    hint_id: Optional[str] = None
    rationale: Optional[str] = None


class ErrorMessage(BaseModel):
    """Сообщение об ошибке, отправляемое сервером клиенту.

    Поля:
        code: Код ошибки (строковый идентификатор).
        message: Человекочитаемое описание ошибки.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    code: str
    message: str


class StatusMessage(BaseModel):
    """Сообщение о текущем статусе обработки.

    Поля:
        status: Текущий статус — «transcribing», «generating_hints», «ready» или «error».
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["status"] = "status"
    status: Literal["transcribing", "generating_hints", "ready", "error", "silent_chunk", "processing"]


class FeedbackAckMessage(BaseModel):
    """Подтверждение получения feedback от сервера."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["feedback_ack"] = "feedback_ack"
    hint_id: str
    status: Literal["recorded", "not_found"] = "recorded"


# ─── REST API ───────────────────────────────────────────────────


class HintTemplate(BaseModel):
    """Шаблон подсказок, доступный через REST API.

    Поля:
        key: Уникальный ключ шаблона.
        label: Человекочитаемое название шаблона.
        argumentative_prompt: Промпт для генерации аргументативных подсказок.
        navigational_prompt: Промпт для генерации навигационных подсказок.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    argumentative_prompt: str
    navigational_prompt: str
