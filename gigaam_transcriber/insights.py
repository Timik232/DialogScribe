"""
Извлечение инсайтов из транскрипций: action items, decisions, suggested steps.

LLM-модуль для автоматического извлечения задач, решений и рекомендуемых
следующих шагов из текста транскрипции.
"""

import json
import logging
import re
from typing import Optional

from gigaam_transcriber.summarizer import LLMClient
from gigaam_transcriber.context_utils import (
    estimate_tokens,
    get_context_budget,
    get_model_context_limit,
    split_into_chunks,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

ACTION_ITEMS_SYSTEM_PROMPT = """\
You are a professional meeting analyst. Analyze the transcription and extract \
action items (tasks) and decisions.

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{
  "action_items": [
    {
      "task": "description of the task",
      "assignee": "name of the person responsible, or null",
      "deadline": "recognized date or deadline, or null",
      "priority": "high|medium|low"
    }
  ],
  "decisions": [
    {
      "decision": "what was decided",
      "context": "why or context"
    }
  ]
}

Rules:
- Extract ONLY concrete, actionable tasks (not abstract statements)
- Assign priority "high" if urgency markers exist ("срочно", "urgent", "ASAP", "как можно скорее", "важно")
- Assign priority "medium" for normal tasks, "low" for minor/optional ones
- Extract assignee names when explicitly mentioned (e.g., "Анна подготовит отчёт" → assignee: "Анна")
- Extract deadlines when mentioned (e.g., "до пятницы", "к 15 числу", "by Friday")
- Include all key decisions made during the discussion
- If the text is empty or contains no tasks/decisions, return empty arrays
- Respond in the same language as the input text"""

SUGGESTED_STEPS_SYSTEM_PROMPT = """\
You are a professional meeting follow-up assistant. Based on the transcription, \
generate recommended next steps.

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{
  "suggested_steps": [
    {
      "step": "description of the recommended action",
      "reason": "why this is important",
      "category": "followup|research|communication|planning"
    }
  ]
}

Categories:
- followup: follow-up actions (send email, schedule meeting, check status)
- research: additional research needed (study materials, investigate topic)
- communication: communication tasks (clarify with speaker, notify team)
- planning: planning tasks (create plan, set deadline, organize process)

Rules:
- Generate 3-8 practical, specific next steps
- Each step should be actionable and realistic
- For meetings: include follow-up emails, task assignments, clarification requests
- For lectures: include study recommendations, additional materials, review topics
- If the text is empty, return an empty array
- Respond in the same language as the input text"""

ACTION_ITEMS_REDUCE_PROMPT = """\
Объедини следующие списки задач и решений, извлечённые из разных частей одной \
транскрипции. Удали дубликаты, сохрани уникальные детали из каждого источника.

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{
  "action_items": [
    {
      "task": "description of the task",
      "assignee": "name or null",
      "deadline": "date or null",
      "priority": "high|medium|low"
    }
  ],
  "decisions": [
    {
      "decision": "what was decided",
      "context": "why or context"
    }
  ]
}"""

SUGGESTED_STEPS_REDUCE_PROMPT = """\
Выбери наиболее релевантные и конкретные следующие шаги из предложенных вариантов. \
Убери дубликаты, оставь самые полезные и практичные рекомендации.

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{
  "suggested_steps": [
    {
      "step": "description",
      "reason": "why",
      "category": "followup|research|communication|planning"
    }
  ]
}"""


# ---------------------------------------------------------------------------
# JSON parsing with fallback
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response with fallback strategies."""
    # Strategy 1: direct json.loads
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract JSON from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: find first { ... } block
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: return raw text with flag
    return {"raw": raw.strip(), "parse_error": True}


# ---------------------------------------------------------------------------
# Core extraction functions
# ---------------------------------------------------------------------------

def extract_action_items(text: str, llm_client: LLMClient) -> dict:
    """Extract action items and decisions from transcription text.

    Uses map-reduce for long texts that exceed 50% of the model's context.
    """
    if not text or not text.strip():
        return {"action_items": [], "decisions": []}

    model = llm_client.config.model
    budget = get_context_budget(model, ACTION_ITEMS_SYSTEM_PROMPT, text)

    if budget["needs_compression"]:
        return _extract_action_items_map_reduce(text, llm_client, budget)

    try:
        raw = llm_client.call(ACTION_ITEMS_SYSTEM_PROMPT, text)
    except Exception as e:
        logger.error("LLM call failed for action items: %s", e)
        raise

    return _validate_action_items(raw)


def _extract_action_items_map_reduce(text: str, llm_client: LLMClient, budget: dict) -> dict:
    """Map-reduce extraction of action items from long text."""
    chunk_max_tokens = min(3000, budget["total"] // 4)
    chunks = split_into_chunks(text, max_tokens=chunk_max_tokens)
    logger.info("Map-reduce action items: %d chunks", len(chunks))

    all_raw_items: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug("Extracting action items from chunk %d/%d", i + 1, len(chunks))
        try:
            raw = llm_client.call(ACTION_ITEMS_SYSTEM_PROMPT, chunk)
            all_raw_items.append(raw)
        except Exception as e:
            logger.warning("Failed to extract from chunk %d: %s", i + 1, e)

    if not all_raw_items:
        return {"action_items": [], "decisions": []}

    combined = "\n\n---\n\n".join(all_raw_items)
    try:
        reduced = llm_client.call(ACTION_ITEMS_REDUCE_PROMPT, combined)
    except Exception as e:
        logger.error("Reduce step failed for action items: %s", e)
        return _validate_action_items(all_raw_items[0])

    return _validate_action_items(reduced)


def _validate_action_items(raw: str) -> dict:
    """Parse and validate action items JSON response."""
    result = _parse_json_response(raw)

    if "parse_error" in result:
        logger.warning("Failed to parse action items JSON, returning raw text")
        return {
            "action_items": [],
            "decisions": [],
            "raw": result.get("raw", raw),
            "parse_error": True,
        }

    action_items = result.get("action_items", [])
    decisions = result.get("decisions", [])

    validated_items = []
    for item in action_items:
        if isinstance(item, dict) and "task" in item:
            validated_items.append({
                "task": str(item.get("task", "")),
                "assignee": item.get("assignee") if item.get("assignee") else None,
                "deadline": item.get("deadline") if item.get("deadline") else None,
                "priority": item.get("priority", "medium")
                    if item.get("priority") in ("high", "medium", "low")
                    else "medium",
            })
    validated_decisions = []
    for d in decisions:
        if isinstance(d, dict) and "decision" in d:
            validated_decisions.append({
                "decision": str(d.get("decision", "")),
                "context": str(d.get("context", "")),
            })

    return {"action_items": validated_items, "decisions": validated_decisions}


def generate_suggested_steps(text: str, llm_client: LLMClient) -> dict:
    """Generate suggested next steps from transcription text.

    Uses map-reduce for long texts that exceed 50% of the model's context.
    """
    if not text or not text.strip():
        return {"suggested_steps": []}

    model = llm_client.config.model
    budget = get_context_budget(model, SUGGESTED_STEPS_SYSTEM_PROMPT, text)

    if budget["needs_compression"]:
        return _generate_steps_map_reduce(text, llm_client, budget)

    try:
        raw = llm_client.call(SUGGESTED_STEPS_SYSTEM_PROMPT, text)
    except Exception as e:
        logger.error("LLM call failed for suggested steps: %s", e)
        raise

    return _validate_suggested_steps(raw)


def _generate_steps_map_reduce(text: str, llm_client: LLMClient, budget: dict) -> dict:
    """Map-reduce generation of suggested steps from long text."""
    chunk_max_tokens = min(3000, budget["total"] // 4)
    chunks = split_into_chunks(text, max_tokens=chunk_max_tokens)
    logger.info("Map-reduce suggested steps: %d chunks", len(chunks))

    all_raw_steps: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug("Generating steps from chunk %d/%d", i + 1, len(chunks))
        try:
            raw = llm_client.call(SUGGESTED_STEPS_SYSTEM_PROMPT, chunk)
            all_raw_steps.append(raw)
        except Exception as e:
            logger.warning("Failed to generate steps from chunk %d: %s", i + 1, e)

    if not all_raw_steps:
        return {"suggested_steps": []}

    combined = "\n\n---\n\n".join(all_raw_steps)
    try:
        reduced = llm_client.call(SUGGESTED_STEPS_REDUCE_PROMPT, combined)
    except Exception as e:
        logger.error("Reduce step failed for suggested steps: %s", e)
        return _validate_suggested_steps(all_raw_steps[0])

    return _validate_suggested_steps(reduced)


def _validate_suggested_steps(raw: str) -> dict:
    """Parse and validate suggested steps JSON response."""
    result = _parse_json_response(raw)

    if "parse_error" in result:
        logger.warning("Failed to parse suggested steps JSON, returning raw text")
        return {
            "suggested_steps": [],
            "raw": result.get("raw", raw),
            "parse_error": True,
        }

    suggested_steps = result.get("suggested_steps", [])

    validated = []
    for step in suggested_steps:
        if isinstance(step, dict) and "step" in step:
            validated.append({
                "step": str(step.get("step", "")),
                "reason": str(step.get("reason", "")),
                "category": step.get("category", "planning")
                    if step.get("category") in ("followup", "research", "communication", "planning")
                    else "planning",
            })

    return {"suggested_steps": validated}


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_insights_txt(action_items: list, decisions: list, suggested_steps: list) -> str:
    """Format insights as plain text for export."""
    lines = []

    # Action Items
    lines.append("=" * 60)
    lines.append("ЗАДАЧИ (ACTION ITEMS)")
    lines.append("=" * 60)
    lines.append("")

    if action_items:
        for i, item in enumerate(action_items, 1):
            priority = item.get("priority", "medium")
            assignee = item.get("assignee")
            deadline = item.get("deadline")
            task = item.get("task", "")
            priority_label = {"high": "ВЫСОКИЙ", "medium": "СРЕДНИЙ", "low": "НИЗКИЙ"}.get(priority, "СРЕДНИЙ")

            lines.append(f"[{i}] {task}")
            lines.append(f"    Приоритет: {priority_label}")
            if assignee:
                lines.append(f"    Ответственный: {assignee}")
            if deadline:
                lines.append(f"    Срок: {deadline}")
            lines.append("")
    else:
        lines.append("Задачи не найдены.")
        lines.append("")

    # Decisions
    lines.append("=" * 60)
    lines.append("РЕШЕНИЯ (DECISIONS)")
    lines.append("=" * 60)
    lines.append("")

    if decisions:
        for i, d in enumerate(decisions, 1):
            lines.append(f"[{i}] {d.get('decision', '')}")
            if d.get("context"):
                lines.append(f"    Контекст: {d['context']}")
            lines.append("")
    else:
        lines.append("Решения не найдены.")
        lines.append("")

    # Suggested Steps
    lines.append("=" * 60)
    lines.append("РЕКОМЕНДУЕМЫЕ СЛЕДУЮЩИЕ ШАГИ (SUGGESTED STEPS)")
    lines.append("=" * 60)
    lines.append("")

    if suggested_steps:
        category_labels = {
            "followup": "Фоллоу-ап",
            "research": "Исследование",
            "communication": "Коммуникация",
            "planning": "Планирование",
        }
        for i, step in enumerate(suggested_steps, 1):
            cat = step.get("category", "planning")
            cat_label = category_labels.get(cat, cat)
            lines.append(f"[{i}] {step.get('step', '')}")
            lines.append(f"    Категория: {cat_label}")
            if step.get("reason"):
                lines.append(f"    Почему: {step['reason']}")
            lines.append("")
    else:
        lines.append("Рекомендации не найдены.")
        lines.append("")

    return "\n".join(lines)
