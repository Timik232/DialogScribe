"""Tests for gigaam_transcriber.insights module."""

import json
from unittest.mock import MagicMock

import pytest

from gigaam_transcriber.insights import (
    _parse_json_response,
    extract_action_items,
    generate_suggested_steps,
    export_insights_txt,
)


def _mock_llm(response_text: str) -> MagicMock:
    mock = MagicMock()
    mock.call.return_value = response_text
    return mock


VALID_ACTION_ITEMS_JSON = json.dumps({
    "action_items": [
        {"task": "Подготовить отчёт", "assignee": "Анна", "deadline": "до пятницы", "priority": "high"},
        {"task": "Отправить email", "assignee": None, "deadline": None, "priority": "medium"},
    ],
    "decisions": [
        {"decision": "Перейти на новый фреймворк", "context": "Текущий устарел"},
    ],
})

VALID_SUGGESTED_STEPS_JSON = json.dumps({
    "suggested_steps": [
        {"step": "Отправить follow-up email", "reason": "Закрепить договорённости", "category": "followup"},
        {"step": "Изучить документацию", "reason": "Для понимания нового фреймворка", "category": "research"},
    ],
})


class TestParseJsonResponse:
    def test_valid_json(self):
        raw = '{"key": "value"}'
        assert _parse_json_response(raw) == {"key": "value"}

    def test_json_in_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert _parse_json_response(raw) == {"key": "value"}

    def test_json_in_plain_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        assert _parse_json_response(raw) == {"key": "value"}

    def test_json_embedded_in_text(self):
        raw = 'Here is the result:\n{"key": "value"}\nEnd.'
        assert _parse_json_response(raw) == {"key": "value"}

    def test_invalid_json_returns_raw(self):
        raw = "This is not JSON at all"
        result = _parse_json_response(raw)
        assert "parse_error" in result
        assert result["raw"] == raw


class TestExtractActionItems:
    def test_valid_json(self):
        llm = _mock_llm(VALID_ACTION_ITEMS_JSON)
        result = extract_action_items("Some text", llm)

        assert len(result["action_items"]) == 2
        assert result["action_items"][0]["task"] == "Подготовить отчёт"
        assert result["action_items"][0]["assignee"] == "Анна"
        assert result["action_items"][0]["priority"] == "high"
        assert len(result["decisions"]) == 1

    def test_empty_text(self):
        llm = MagicMock()
        result = extract_action_items("", llm)
        assert result == {"action_items": [], "decisions": []}
        llm.call.assert_not_called()

    def test_whitespace_only_text(self):
        llm = MagicMock()
        result = extract_action_items("   \n  ", llm)
        assert result == {"action_items": [], "decisions": []}

    def test_invalid_json_fallback(self):
        llm = _mock_llm("This is not JSON")
        result = extract_action_items("Some text", llm)
        assert "parse_error" in result
        assert result["action_items"] == []
        assert result["decisions"] == []

    def test_partial_validation(self):
        raw = json.dumps({
            "action_items": [
                {"task": "Valid task", "priority": "high"},
                {"no_task_field": True},
            ],
            "decisions": [
                {"decision": "Valid decision", "context": "ok"},
                {"not_a_decision": True},
            ],
        })
        llm = _mock_llm(raw)
        result = extract_action_items("Text", llm)
        assert len(result["action_items"]) == 1
        assert result["action_items"][0]["task"] == "Valid task"
        assert len(result["decisions"]) == 1

    def test_invalid_priority_normalized(self):
        raw = json.dumps({
            "action_items": [{"task": "Task", "priority": "urgent"}],
            "decisions": [],
        })
        llm = _mock_llm(raw)
        result = extract_action_items("Text", llm)
        assert result["action_items"][0]["priority"] == "medium"


class TestGenerateSuggestedSteps:
    def test_valid_json(self):
        llm = _mock_llm(VALID_SUGGESTED_STEPS_JSON)
        result = generate_suggested_steps("Some text", llm)
        assert len(result["suggested_steps"]) == 2
        assert result["suggested_steps"][0]["step"] == "Отправить follow-up email"
        assert result["suggested_steps"][0]["category"] == "followup"

    def test_empty_text(self):
        llm = MagicMock()
        result = generate_suggested_steps("", llm)
        assert result == {"suggested_steps": []}

    def test_invalid_json_fallback(self):
        llm = _mock_llm("Not JSON")
        result = generate_suggested_steps("Text", llm)
        assert "parse_error" in result
        assert result["suggested_steps"] == []

    def test_invalid_category_normalized(self):
        raw = json.dumps({
            "suggested_steps": [{"step": "Do something", "reason": "why", "category": "unknown"}],
        })
        llm = _mock_llm(raw)
        result = generate_suggested_steps("Text", llm)
        assert result["suggested_steps"][0]["category"] == "planning"


class TestExportInsightsTxt:
    def test_all_sections(self):
        action_items = [{"task": "Task 1", "assignee": "Anna", "deadline": "Friday", "priority": "high"}]
        decisions = [{"decision": "Decided X", "context": "Because Y"}]
        suggested_steps = [{"step": "Send email", "reason": "Follow up", "category": "followup"}]

        result = export_insights_txt(action_items, decisions, suggested_steps)
        assert "ЗАДАЧИ" in result
        assert "Task 1" in result
        assert "Anna" in result
        assert "РЕШЕНИЯ" in result
        assert "Decided X" in result
        assert "РЕКОМЕНДУЕМЫЕ" in result
        assert "Send email" in result

    def test_empty_insights(self):
        result = export_insights_txt([], [], [])
        assert "не найдены" in result
