"""
Оркестрация Autoflow: транскрипция → саммари → инсайты → майндмэп.

Стадии пайплайна описаны явным enum'ом :class:`AutoflowStage`; каждое
событие прогресса несёт структурированную стадию (:class:`StageEvent`),
а не выводится из текста сообщения. Отключённые флагами стадии
(``include_summary`` / ``include_insights`` / ``include_mindmap``)
пропускаются: эмитится ровно одно событие ``stage="skipped"`` с полем
``skipped_stage``, и их выходы отсутствуют в результате.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.data_models import TranscriptionResult
from gigaam_transcriber.insights import extract_action_items, generate_suggested_steps
from gigaam_transcriber.mindmap import generate_mindmap_markdown
from gigaam_transcriber.summarizer import SUMMARY_TEMPLATES, LLMClient, generate_summary

logger = logging.getLogger(__name__)


class AutoflowStage(str, Enum):
    """Явные стадии пайплайна (порядок объявления = порядок выполнения).

    ``SKIPPED`` — не позиция пайплайна, а маркер события «стадия отключена»;
    такое событие дополнительно несёт ``skipped_stage`` с реальной стадией.
    """

    UPLOAD = "upload"
    TRANSCRIBE = "transcribe"
    SUMMARY = "summary"
    INSIGHTS = "insights"
    MINDMAP = "mindmap"
    COMPLETE = "complete"
    SKIPPED = "skipped"


#: Стадии опциональных LLM-этапов в порядке выполнения.
OPTIONAL_LLM_STAGES = (AutoflowStage.SUMMARY, AutoflowStage.INSIGHTS, AutoflowStage.MINDMAP)


@dataclass(frozen=True)
class StageEvent:
    """Структурированное событие прогресса, передаваемое WS-клиенту."""

    stage: AutoflowStage
    progress: float
    message: str
    skipped_stage: AutoflowStage | None = None


@dataclass
class AutoflowResult:
    transcription_result: TranscriptionResult | None = None
    summary_text: str = ""
    mindmap_md: str = ""
    action_items: dict | None = None
    suggested_steps: dict | None = None
    errors: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)


async def run_autoflow(
    file_path: str,
    template_key: str,
    llm_client: LLMClient,
    config: dict,
    transcriber,
    db: AsyncSession | None = None,
    user_id: str | None = None,
    progress_callback: Callable[[StageEvent], None] | None = None,
    include_insights: bool = False,
    include_summary: bool = True,
    include_mindmap: bool = True,
    model: str | None = None,
) -> AutoflowResult:
    """Выполнить пайплайн; ``model`` пробрасывается в каждый LLM-этап."""
    result = AutoflowResult()
    loop = asyncio.get_event_loop()

    def emit(
        stage: AutoflowStage,
        progress: float,
        message: str,
        skipped_stage: AutoflowStage | None = None,
    ) -> None:
        if progress_callback is not None:
            progress_callback(StageEvent(stage, progress, message, skipped_stage))

    if db and user_id:
        from gigaam_transcriber.template_manager import TemplateManager
        all_templates = await TemplateManager.get_all_templates(db, user_id, SUMMARY_TEMPLATES)
    else:
        all_templates = SUMMARY_TEMPLATES

    # --- transcribe -------------------------------------------------------
    emit(AutoflowStage.TRANSCRIBE, 0.05, "Транскрибация...")
    try:
        t0 = time.monotonic()
        diarization = config.get("diarization", "none")
        denoise = config.get("denoise", "none")
        transcription = await loop.run_in_executor(
            None, lambda: transcriber.transcribe(file_path, diarization=diarization, denoise=denoise)
        )
        result.stage_timings["transcription"] = time.monotonic() - t0
        result.transcription_result = transcription
        emit(AutoflowStage.TRANSCRIBE, 0.35, "Транскрибация завершена")
    except Exception as e:
        logger.exception("Autoflow: transcription failed")
        result.errors.append(f"Транскрибация: {e}")
        emit(AutoflowStage.TRANSCRIBE, 1.0, f"Ошибка транскрибации: {e}")
        return result

    transcription_text = transcription.text or ""
    if not transcription_text.strip():
        result.errors.append("Транскрипция пуста")
        emit(AutoflowStage.TRANSCRIBE, 1.0, "Пустая транскрипция")
        return result

    # --- summary ----------------------------------------------------------
    if not include_summary:
        emit(AutoflowStage.SKIPPED, 0.4, "Саммари отключено", skipped_stage=AutoflowStage.SUMMARY)
    else:
        template = all_templates.get(template_key)
        if not template:
            result.errors.append(f"Шаблон '{template_key}' не найден")
            emit(AutoflowStage.SUMMARY, 1.0, f"Шаблон '{template_key}' не найден")
        else:
            emit(AutoflowStage.SUMMARY, 0.4, "Генерация саммари...")
            try:
                t0 = time.monotonic()
                if template_key in SUMMARY_TEMPLATES:
                    # generate_summary only touches db for template lookup; builtin
                    # keys resolve without it, so run the blocking LLM work in a
                    # worker thread to keep the loop responsive to cancellation.
                    summary_md = await loop.run_in_executor(
                        None,
                        lambda: asyncio.run(
                            generate_summary(
                                transcription_text, template_key, llm_client, model=model
                            )
                        ),
                    )
                else:
                    summary_md = await generate_summary(
                        transcription_text,
                        template_key,
                        llm_client,
                        db=db,
                        user_id=user_id,
                        model=model,
                    )
                result.stage_timings["summary"] = time.monotonic() - t0
                result.summary_text = summary_md
                emit(AutoflowStage.SUMMARY, 0.6, "Саммари создано")
            except Exception as e:
                logger.exception("Autoflow: summary failed")
                result.errors.append(f"Саммари: {e}")
                emit(AutoflowStage.SUMMARY, 0.6, f"Саммари пропущено: {e}")

    # --- insights ---------------------------------------------------------
    if not include_insights:
        emit(AutoflowStage.SKIPPED, 0.65, "Инсайты отключены", skipped_stage=AutoflowStage.INSIGHTS)
    else:
        emit(AutoflowStage.INSIGHTS, 0.65, "Извлечение инсайтов...")
        try:
            t0 = time.monotonic()
            result.action_items = await loop.run_in_executor(
                None, partial(extract_action_items, transcription_text, llm_client, model=model)
            )
            result.suggested_steps = await loop.run_in_executor(
                None, partial(generate_suggested_steps, transcription_text, llm_client, model=model)
            )
            result.stage_timings["insights"] = time.monotonic() - t0
            emit(AutoflowStage.INSIGHTS, 0.75, "Инсайты готовы")
        except Exception as e:
            logger.exception("Autoflow: insights extraction failed")
            result.errors.append(f"Инсайты: {e}")
            emit(AutoflowStage.INSIGHTS, 0.75, f"Инсайты пропущены: {e}")

    # --- mindmap ----------------------------------------------------------
    if not include_mindmap:
        emit(AutoflowStage.SKIPPED, 0.8, "Майндмэп отключён", skipped_stage=AutoflowStage.MINDMAP)
    else:
        emit(AutoflowStage.MINDMAP, 0.8, "Создание майндмэпа...")
        try:
            t0 = time.monotonic()
            md = await loop.run_in_executor(
                None,
                partial(generate_mindmap_markdown, transcription_text, llm_client, model=model),
            )
            result.stage_timings["mindmap"] = time.monotonic() - t0
            result.mindmap_md = md
            emit(AutoflowStage.MINDMAP, 0.95, "Майндмэп создан")
        except Exception as e:
            logger.exception("Autoflow: mindmap failed")
            result.errors.append(f"Майндмэп: {e}")
            emit(AutoflowStage.MINDMAP, 0.95, f"Майндмэп пропущен: {e}")

    return result
