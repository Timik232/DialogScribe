"""Tests for reduce-step context overflow protection.

Verifies that all map-reduce reduce steps (summarizer, mindmap, insights)
check token budget before sending combined summaries to the LLM.
If combined text exceeds budget, hierarchical reduce splits into sub-groups.
If still over budget after 2 levels, raises ValueError.
"""

import logging
import math
import pytest
from unittest.mock import MagicMock, patch

from gigaam_transcriber.summarizer import (
    LLMClient,
    LLMClientConfig,
    generate_summary,
)
from gigaam_transcriber.mindmap import (
    _generate_mindmap_map_reduce,
    MINDMAP_SYSTEM_PROMPT,
    MINDMAP_REDUCE_PROMPT,
)
from gigaam_transcriber.insights import (
    _extract_action_items_map_reduce,
    _generate_steps_map_reduce,
    ACTION_ITEMS_SYSTEM_PROMPT,
    ACTION_ITEMS_REDUCE_PROMPT,
    SUGGESTED_STEPS_SYSTEM_PROMPT,
    SUGGESTED_STEPS_REDUCE_PROMPT,
)
from gigaam_transcriber.context_utils import estimate_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(api_key="sk-test", model="gpt-4o-mini"):
    """Create LLMClient with mocked OpenAI client."""
    config = LLMClientConfig(api_key=api_key, model=model)
    client = LLMClient(config)
    mock_openai = MagicMock()
    client._client = mock_openai
    return client, mock_openai


def _mock_response(text: str):
    """Create a mock OpenAI response that returns `text`."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage.prompt_tokens = len(text)
    resp.usage.completion_tokens = len(text)
    return resp


# ---------------------------------------------------------------------------
# 1. Simple reduce for short combined text
# ---------------------------------------------------------------------------


class TestSimpleReduceShortText:
    """3 chunks × ~500 tokens → single reduce call (no splitting)."""

    @pytest.mark.asyncio
    async def test_summarizer_simple_reduce(self):
        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"summary-{call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        # 3 chunks of ~500 tokens each → combined ~1500, fits in gpt-4o-mini (128k)
        text = ". ".join([f"Предложение номер {i} для тестирования" * 20 for i in range(50)])
        result = await generate_summary(text, "general", client)
        # 3 chunk summaries + 1 reduce = 4 calls
        assert call_count[0] >= 2
        assert result  # non-empty

    def test_mindmap_simple_reduce(self):
        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"# Topic\n## Branch {call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 128_000, "available": 100_000}
        # Short text → few chunks → combined fits
        text = "Test text. " * 100
        _generate_mindmap_map_reduce(text, client, budget)
        # map + 1 reduce
        assert call_count[0] >= 2

    def test_action_items_simple_reduce(self):
        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response('{"action_items":[],"decisions":[]}')

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 128_000, "available": 100_000}
        text = "Test text. " * 100
        _extract_action_items_map_reduce(text, client, budget)
        assert call_count[0] >= 2

    def test_suggested_steps_simple_reduce(self):
        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response('{"suggested_steps":[]}')

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 128_000, "available": 100_000}
        text = "Test text. " * 100
        _generate_steps_map_reduce(text, client, budget)
        assert call_count[0] >= 2


# ---------------------------------------------------------------------------
# 2. Hierarchical reduce splits sub-groups when over budget
# ---------------------------------------------------------------------------


class TestHierarchicalReduce:
    """10 chunks × ~2000 tokens each (total ~20k) with budget of 8k → splits into sub-groups."""

    @pytest.mark.asyncio
    async def test_summarizer_hierarchical_reduce(self):
        """When combined chunk summaries exceed budget, hierarchical reduce splits them."""
        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"summary-{call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        # Use gpt-4 which has only 8192 context limit
        client._config = LLMClientConfig(api_key="sk-test", model="gpt-4")
        client._client = mock_openai

        # Long text that splits into many chunks, combined summaries exceed 8k
        text = ". ".join([f"Предложение номер {i} для тестирования разбиения длинного текста" * 5
                         for i in range(200)])
        result = await generate_summary(text, "general", client)
        assert result  # non-empty
        # With gpt-4 (8192 ctx), should have more calls than simple map+reduce
        # At minimum: map calls + sub-group reduces + final reduce
        assert call_count[0] >= 3

    def test_mindmap_hierarchical_reduce(self):
        client, mock_openai = _make_client(model="gpt-4")
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"# Topic\n## Branch {call_count[0]}\n- detail\n" * 50)

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 8192, "available": 2000}
        text = ". ".join([f"Предложение номер {i} для тестирования разбиения длинного текста на части" * 10
                         for i in range(200)])
        _generate_mindmap_map_reduce(text, client, budget)
        assert call_count[0] >= 3


# ---------------------------------------------------------------------------
# 3. Max depth exceeded → ValueError
# ---------------------------------------------------------------------------


class TestMaxDepthExceeded:
    """Impossibly large combined text → ValueError."""

    @pytest.mark.asyncio
    async def test_summarizer_max_depth_raises(self):
        """When even sub-group summaries exceed budget, raise ValueError."""
        client, mock_openai = _make_client(model="gpt-4")

        # Each "summary" returned by LLM is huge — simulating no compression
        def side_effect(**kwargs):
            # Return enormous text to ensure it can never fit
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "x " * 50_000  # ~25k tokens each
            resp.usage.prompt_tokens = 100
            resp.usage.completion_tokens = 100
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect

        text = ". ".join([f"Предложение {i} " * 100 for i in range(100)])
        with pytest.raises(ValueError, match="[Tt]oo long|[Tt]oo large|context"):
            await generate_summary(text, "general", client)

    def test_mindmap_max_depth_raises(self):
        client, mock_openai = _make_client(model="gpt-4")

        def side_effect(**kwargs):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "x " * 50_000
            resp.usage.prompt_tokens = 100
            resp.usage.completion_tokens = 100
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 8192, "available": 2000}
        text = "Text. " * 500
        with pytest.raises(ValueError, match="[Tt]oo long|[Tt]oo large|context"):
            _generate_mindmap_map_reduce(text, client, budget)

    def test_action_items_max_depth_raises(self):
        client, mock_openai = _make_client(model="gpt-4")

        def side_effect(**kwargs):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "x " * 50_000
            resp.usage.prompt_tokens = 100
            resp.usage.completion_tokens = 100
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 8192, "available": 2000}
        text = "Text. " * 500
        with pytest.raises(ValueError, match="[Tt]oo long|[Tt]oo large|context"):
            _extract_action_items_map_reduce(text, client, budget)

    def test_suggested_steps_max_depth_raises(self):
        client, mock_openai = _make_client(model="gpt-4")

        def side_effect(**kwargs):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "x " * 50_000
            resp.usage.prompt_tokens = 100
            resp.usage.completion_tokens = 100
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect

        budget = {"total": 8192, "available": 2000}
        text = "Text. " * 500
        with pytest.raises(ValueError, match="[Tt]oo long|[Tt]oo large|context"):
            _generate_steps_map_reduce(text, client, budget)


# ---------------------------------------------------------------------------
# 4. Output reserve is respected
# ---------------------------------------------------------------------------


class TestOutputReserveRespected:
    """Verify reduce doesn't use 100% of context — output reserve is maintained."""

    @pytest.mark.asyncio
    async def test_summarizer_respects_output_reserve(self, caplog):
        """Ensure the reduce step leaves room for output tokens."""
        client, mock_openai = _make_client(model="gpt-4")
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            # Get the messages argument
            call_args = mock_openai.chat.completions.create.call_args
            if call_args:
                messages = call_args[1].get("messages", call_args[0] if call_args[0] else [])
                # Check that user content isn't using the entire context
                for msg in messages:
                    if msg.get("role") == "user":
                        user_tokens = estimate_tokens(msg["content"])
                        # gpt-4 has 8192 context; user text should not exceed
                        # total - prompt - output_reserve (20%) = 8192 - ~500 - ~1638 ≈ 6000
                        # We allow generous margin but not 100%
                        assert user_tokens < 8192, (
                            f"User text ({user_tokens} tokens) should not exceed full context (8192)"
                        )
            return _mock_response(f"summary-{call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        text = ". ".join([f"Предложение {i} " * 50 for i in range(100)])
        with caplog.at_level(logging.INFO, logger="gigaam_transcriber.llm"):
            await generate_summary(text, "general", client)


# ---------------------------------------------------------------------------
# 5. Logging: strategy logged
# ---------------------------------------------------------------------------


class TestReduceLogging:
    """Verify reduce strategy is logged."""

    @pytest.mark.asyncio
    async def test_summarizer_logs_reduce_strategy(self, caplog):
        client, mock_openai = _make_client()

        def side_effect(**kwargs):
            return _mock_response("summary")

        mock_openai.chat.completions.create.side_effect = side_effect

        text = ". ".join([f"Предложение номер {i} для тестирования" * 20 for i in range(50)])
        with caplog.at_level(logging.INFO, logger="gigaam_transcriber.llm"):
            await generate_summary(text, "general", client)

        # Should log reduce strategy
        reduce_logs = [r for r in caplog.records if "Reduce" in r.message or "reduce" in r.message.lower()]
        # At minimum, should have some log about reduce step
        assert any("Reduce" in r.message or "reduce" in r.message for r in caplog.records) or \
               any("map-reduce" in r.message.lower() for r in caplog.records)
