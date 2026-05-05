"""
Оркестрация Autoflow: транскрипция → саммари → майндмэп.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.data_models import TranscriptionResult
from gigaam_transcriber.insights import extract_action_items, generate_suggested_steps
from gigaam_transcriber.summarizer import LLMClient, generate_summary, SUMMARY_TEMPLATES
from gigaam_transcriber.mindmap import generate_mindmap_markdown, render_mindmap_html

logger = logging.getLogger(__name__)


@dataclass
class AutoflowResult:
    transcription_result: Optional[TranscriptionResult] = None
    summary_text: str = ""
    mindmap_html: str = ""
    mindmap_md: str = ""
    action_items: Optional[dict] = None
    suggested_steps: Optional[dict] = None
    errors: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)


async def run_autoflow(
    file_path: str,
    template_key: str,
    llm_client: LLMClient,
    config: dict,
    transcriber,
    db: Optional[AsyncSession] = None,
    user_id: Optional[str] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    include_insights: bool = False,
) -> AutoflowResult:
    result = AutoflowResult()

    if progress_callback:
        progress_callback("🔄 Транскрибация...", 0.05)

    if db and user_id:
        from gigaam_transcriber.template_manager import TemplateManager
        all_templates = await TemplateManager.get_all_templates(db, user_id, SUMMARY_TEMPLATES)
    else:
        all_templates = SUMMARY_TEMPLATES

    try:
        t0 = time.monotonic()
        diarization = config.get("diarization", "none")
        denoise = config.get("denoise", "none")
        loop = asyncio.get_event_loop()
        transcription = await loop.run_in_executor(
            None, lambda: transcriber.transcribe(file_path, diarization=diarization, denoise=denoise)
        )
        result.stage_timings["transcription"] = time.monotonic() - t0
        result.transcription_result = transcription

        if progress_callback:
            progress_callback("✅ Транскрибация завершена", 0.35)
    except Exception as e:
        logger.exception("Autoflow: transcription failed")
        result.errors.append(f"Транскрибация: {e}")
        if progress_callback:
            progress_callback(f"❌ Ошибка транскрибации: {e}", 1.0)
        return result

    transcription_text = transcription.text or ""
    if not transcription_text.strip():
        result.errors.append("Транскрипция пуста")
        if progress_callback:
            progress_callback("❌ Пустая транскрипция", 1.0)
        return result

    if progress_callback:
        progress_callback("📝 Генерация саммари...", 0.4)

    template = all_templates.get(template_key)
    if not template:
        result.errors.append(f"Шаблон '{template_key}' не найден")
        if progress_callback:
            progress_callback(f"❌ Шаблон '{template_key}' не найден", 1.0)
        return result

    try:
        t0 = time.monotonic()
        summary_md = await generate_summary(transcription_text, template_key, llm_client, db=db, user_id=user_id)
        result.stage_timings["summary"] = time.monotonic() - t0
        result.summary_text = summary_md

        if progress_callback:
            progress_callback("✅ Саммари создано", 0.7)
    except Exception as e:
        logger.exception("Autoflow: summary failed")
        result.errors.append(f"Саммари: {e}")
        if progress_callback:
            progress_callback(f"⚠️ Саммари пропущено: {e}", 0.7)

    if result.summary_text:
        if progress_callback:
            progress_callback("🗺️ Создание майндмэпа...", 0.75)

        try:
            t0 = time.monotonic()
            md = generate_mindmap_markdown(transcription_text, llm_client)
            html = render_mindmap_html(md, uid="autoflow")
            result.stage_timings["mindmap"] = time.monotonic() - t0
            result.mindmap_md = md
            result.mindmap_html = html
        except Exception as e:
            logger.exception("Autoflow: mindmap failed")
            result.errors.append(f"Майндмэп: {e}")

    if include_insights and transcription_text:
        if progress_callback:
            progress_callback("📋 Извлечение инсайтов...", 0.85)

        try:
            t0 = time.monotonic()
            result.action_items = extract_action_items(transcription_text, llm_client)
            result.suggested_steps = generate_suggested_steps(transcription_text, llm_client)
            result.stage_timings["insights"] = time.monotonic() - t0
        except Exception as e:
            logger.exception("Autoflow: insights extraction failed")
            result.errors.append(f"Инсайты: {e}")

        if progress_callback:
            progress_callback("✅ Инсайты готовы", 0.95)

    if progress_callback:
        status = "✅ Готово!" if not result.errors else f"⚠️ Готово с ошибками: {len(result.errors)}"
        progress_callback(status, 1.0)

    return result
