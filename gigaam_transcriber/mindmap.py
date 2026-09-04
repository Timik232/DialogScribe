"""
Генерация mind map из транскрипций.

LLM генерирует иерархический Markdown, который отдаётся клиентам как есть
и рендерится на стороне фронтенда (Markmap). Серверные HTML-эндпоинты
/mindmap/{uid} и /mindmap-static удалены — executable HTML больше не
генерируется бэкендом.
"""

import html
import logging
import re
from typing import Optional

from gigaam_transcriber.summarizer import LLMClient
from gigaam_transcriber.context_utils import (
    estimate_tokens_accurate,
    get_context_budget,
    split_into_chunks,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt для генерации mind map
# ---------------------------------------------------------------------------

MINDMAP_SYSTEM_PROMPT = """\
Ты — эксперт по структурированию информации. Проанализируй транскрипцию \
и создай иерархическую структуру mind map в формате Markdown.

ПРАВИЛА:
1. Используй ТОЛЬКО заголовки (# , ## , ### ) для иерархии — это важно для рендеринга
2. Корневой узел — один заголовок # (тема/название)
3. Основные ветви — заголовки ##
4. Подветви — заголовки ###
5. Конкретные детали — маркированные списки под заголовками
6. НЕ используй нумерованные списки, таблицы или блоки кода
7. Каждый узел должен быть кратким (до 10 слов)
8. Структура должна быть сбалансированной (3-7 основных ветвей)

ПРИМЕР:
# Тема встречи
## Участники
### Иванов
- предложил план
### Петров
- согласовал бюджет
## Решения
### Бюджет утверждён
- 500 000 рублей
## Задачи
### Разработка
- дедлайн: 15 марта
- ответственный: Иванов

Генерируй ТОЛЬКО Markdown-структуру без пояснений. Всё на русском языке."""

MINDMAP_REDUCE_PROMPT = """\
Перед тобой несколько фрагментов mind map (Markmap Markdown), созданных из \
разных частей одной транскрипции. Объедини их в один целостный Markmap \
Markdown без повторов и противоречий.

ПРАВИЛА:
1. Один корневой заголовок # (общая тема)
2. 3-7 основных ветвей ##
3. Подветви ### и детали списками
4. Убери дубликаты, сохрани уникальные детали
5. Генерируй ТОЛЬКО Markdown без пояснений"""

# ---------------------------------------------------------------------------
# Валидация и post-processing
# ---------------------------------------------------------------------------


def validate_mindmap_markdown(md_text: str) -> bool:
    """
    Проверить, что Markdown подходит для mind map.

    Returns:
        True если структура корректна
    """
    lines = md_text.strip().split("\n")
    has_h1 = any(line.startswith("# ") for line in lines)
    has_h2 = any(line.startswith("## ") for line in lines)
    return has_h1 and has_h2


def postprocess_mindmap_markdown(md_text: str) -> str:
    """
    Post-processing Markdown для mind map.

    - Убирает блоки кода
    - Убирает таблицы
    - Гарантирует наличие корневого H1
    - Исправляет пропуски в иерархии
    """
    lines = md_text.strip().split("\n")
    processed: list[str] = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Пропускаем таблицы
        if "|" in line and re.match(r"^[\s|:-]+$", line):
            continue

        # Пропускаем пустые заголовки
        if re.match(r"^#{1,4}\s*$", line):
            continue

        processed.append(line)

    # Гарантируем H1 в начале
    if processed and not processed[0].startswith("# "):
        processed.insert(0, "# Тема транскрипции")

    if not any(l.startswith("# ") for l in processed):
        processed.insert(0, "# Тема транскрипции")

    return "\n".join(processed)


def _markdown_to_tree_html(md_text: str) -> str:
    """Конвертировать Markdown в HTML-дерево для fallback-режима."""
    lines = md_text.strip().split("\n")
    html_parts: list[str] = []

    for line in lines:
        if not line.strip():
            continue
        if line.startswith("# "):
            html_parts.append(f'<strong style="font-size:1.2em;">{html.escape(line[2:])}</strong>')
        elif line.startswith("## "):
            html_parts.append(
                f'<div style="margin-left:20px; margin-top:8px;"><span style="color:#1f77b4; font-weight:600;">{html.escape(line[3:])}</span>'
            )
        elif line.startswith("### "):
            html_parts.append(
                f'<div style="margin-left:40px; margin-top:4px;"><span style="color:#2ca02c; font-weight:600;">{html.escape(line[4:])}</span>'
            )
        elif line.startswith("- "):
            html_parts.append(f'<div style="margin-left:60px;">• {html.escape(line[2:])}</div>')
        else:
            html_parts.append(f'<div style="margin-left:20px;">{html.escape(line)}</div>')

    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Санитизация Markdown
# ---------------------------------------------------------------------------


def _sanitize_markdown(md_text: str) -> str:
    """Sanitize Markdown to prevent XSS in embedded HTML."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", md_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', "", text, flags=re.IGNORECASE)
    for tag in ("iframe", "object", "embed", "form", "input"):
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(rf"<{tag}[^>]*/?\s*>", "", text, flags=re.IGNORECASE)
    return text


# Упрощённый fallback без CDN
_FALLBACK_ONLY_TEMPLATE = """\
<div style="font-family: Arial, sans-serif; padding: 16px; border: 1px solid #e0e0e0; border-radius: 8px; background: #fafafa;">
<h3 style="margin-top:0;">🧠 Mind Map (текстовый режим)</h3>
<p style="color:#666; font-size:0.9em;">Markmap.js не загружен. Отображается текстовая иерархия.</p>
<hr style="border:none;border-top:1px solid #eee;">
<div style="white-space:pre-wrap; font-size:0.95em; line-height:1.5;">{tree_html}</div>
</div>"""


# ---------------------------------------------------------------------------
# Генерация mind map
# ---------------------------------------------------------------------------


def generate_mindmap_markdown(
    transcription_text: str,
    llm_client: LLMClient,
    model: Optional[str] = None,
) -> str:
    """Сгенерировать Markdown для mind map из транскрипции.

    Uses map-reduce for long texts that exceed 50% of the model's context.
    """
    logger.info("Mindmap: calling LLM (text length=%d chars)", len(transcription_text))

    effective_model = model or llm_client.config.model
    budget = get_context_budget(effective_model, MINDMAP_SYSTEM_PROMPT, transcription_text)

    if budget["needs_compression"]:
        raw_md = _generate_mindmap_map_reduce(transcription_text, llm_client, budget, model=model)
    else:
        raw_md = llm_client.call(
            MINDMAP_SYSTEM_PROMPT,
            transcription_text,
            max_tokens=4096,
            model_override=model,
        )

    logger.info(
        "Mindmap: LLM returned %d chars, first 200 chars: %s",
        len(raw_md),
        raw_md[:200].replace("\n", "\\n"),
    )

    processed = postprocess_mindmap_markdown(raw_md)

    logger.info(
        "Mindmap: post-processed %d chars, first 200 chars: %s",
        len(processed),
        processed[:200].replace("\n", "\\n"),
    )

    valid = validate_mindmap_markdown(processed)
    if not valid:
        logger.warning(
            "Mindmap: validation FAILED (has_h1=%s, has_h2=%s), using fallback structure",
            any(l.startswith("# ") for l in processed.split("\n")),
            any(l.startswith("## ") for l in processed.split("\n")),
        )
        processed = (
            "# Тема транскрипции\n"
            "## Ключевые моменты\n"
            "- Не удалось построить полную структуру\n"
            "## Основное содержание\n"
        ) + processed
    else:
        logger.info("Mindmap: validation passed")

    h1_count = sum(
        1 for l in processed.split("\n") if l.startswith("# ") and not l.startswith("## ")
    )
    h2_count = sum(
        1 for l in processed.split("\n") if l.startswith("## ") and not l.startswith("### ")
    )
    h3_count = sum(1 for l in processed.split("\n") if l.startswith("### "))
    bullet_count = sum(1 for l in processed.split("\n") if l.startswith("- "))
    logger.info(
        "Mindmap: structure summary — H1=%d, H2=%d, H3=%d, bullets=%d",
        h1_count,
        h2_count,
        h3_count,
        bullet_count,
    )

    return processed


def _generate_mindmap_map_reduce(text: str, llm_client: LLMClient, budget: dict, model: Optional[str] = None) -> str:
    """Map-reduce mindmap generation for long texts."""
    effective_model = model or llm_client.config.model
    chunk_max_tokens = min(3000, budget["total"] // 4)
    chunks = split_into_chunks(text, max_tokens=chunk_max_tokens)
    logger.info("Map-reduce mindmap: %d chunks", len(chunks))

    subtrees: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug("Generating subtree from chunk %d/%d", i + 1, len(chunks))
        try:
            subtree = llm_client.call(MINDMAP_SYSTEM_PROMPT, chunk, max_tokens=4096, model_override=model)
            subtrees.append(subtree)
        except Exception as e:
            logger.warning("Failed to generate subtree from chunk %d: %s", i + 1, e)

    if not subtrees:
        return "# Тема транскрипции\n## Ошибка обработки\n- Не удалось сгенерировать структуру"

    combined = "\n\n---\n\n".join(subtrees)
    budget_check = get_context_budget(
        effective_model, MINDMAP_REDUCE_PROMPT, combined,
    )

    if budget_check["available"] >= estimate_tokens_accurate(combined, effective_model):
        logger.info(
            "Mindmap reduce: %d subtrees, %d tokens, strategy=simple",
            len(subtrees),
            budget_check["used_text"],
        )
        try:
            return llm_client.call(MINDMAP_REDUCE_PROMPT, combined, max_tokens=4096, model_override=model)
        except Exception as e:
            logger.error("Reduce step failed for mindmap: %s", e)
            return subtrees[0]

    logger.info(
        "Mindmap reduce: %d subtrees, %d tokens, strategy=hierarchical",
        len(subtrees),
        budget_check["used_text"],
    )
    prompt_tokens = estimate_tokens_accurate(MINDMAP_REDUCE_PROMPT, effective_model)
    available_per_group = budget_check["total"] - prompt_tokens - budget_check["output_reserve"]

    subtrees_per_group = 1
    for size in range(len(subtrees), 0, -1):
        test_combined = "\n\n---\n\n".join(subtrees[:size])
        if estimate_tokens_accurate(test_combined, effective_model) <= available_per_group:
            subtrees_per_group = size
            break

    sub_results: list[str] = []
    for i in range(0, len(subtrees), subtrees_per_group):
        group = subtrees[i : i + subtrees_per_group]
        group_combined = "\n\n---\n\n".join(group)
        try:
            sub_result = llm_client.call(MINDMAP_REDUCE_PROMPT, group_combined, max_tokens=4096, model_override=model)
            sub_results.append(sub_result)
        except Exception as e:
            logger.warning("Sub-group reduce failed: %s", e)
            sub_results.append(group[0])

    if not sub_results:
        return subtrees[0]

    final_combined = "\n\n---\n\n".join(sub_results)
    final_check = get_context_budget(
        effective_model, MINDMAP_REDUCE_PROMPT, final_combined,
    )
    if final_check["available"] >= estimate_tokens_accurate(final_combined, effective_model):
        try:
            return llm_client.call(MINDMAP_REDUCE_PROMPT, final_combined, max_tokens=4096, model_override=model)
        except Exception as e:
            logger.error("Final reduce failed for mindmap: %s", e)
            return sub_results[0]

    raise ValueError(
        f"Text too long for context: {final_check['used_text']} tokens needed, "
        f"{final_check['available']} available (model={effective_model})"
    )


def render_mindmap_fallback(md_text: str) -> str:
    """
    Рендерить mind map в fallback HTML (без Markmap.js).

    Args:
        md_text: Иерархический Markdown

    Returns:
        HTML-строка для gr.HTML()
    """
    tree_html = _markdown_to_tree_html(md_text)
    return _FALLBACK_ONLY_TEMPLATE.format(tree_html=tree_html)
