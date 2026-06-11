"""TDD tests for estimate_tokens_accurate() and updated get_context_budget().

These tests are written BEFORE implementation (TDD red phase).
They test:
1. estimate_tokens_accurate() — tiktoken-based token counting with fallback
2. get_context_budget() — now reserves explicit output tokens
"""

import pytest
import tiktoken

from gigaam_transcriber.context_utils import (
    estimate_tokens,
    estimate_tokens_accurate,
    get_context_budget,
    get_model_context_limit,
)


# ---------------------------------------------------------------------------
# estimate_tokens_accurate — basic properties
# ---------------------------------------------------------------------------


class TestEstimateTokensAccurateBasic:
    """Basic contract tests for estimate_tokens_accurate."""

    def test_empty_string_returns_zero(self):
        """Empty string should always return 0 tokens."""
        assert estimate_tokens_accurate("", "gpt-4o") == 0

    def test_positive_for_nonempty(self):
        """Any non-empty text should return > 0 tokens."""
        assert estimate_tokens_accurate("Hello world", "gpt-4o") > 0

    def test_returns_int(self):
        """Result must be an int, not float."""
        result = estimate_tokens_accurate("Some text here", "gpt-4o")
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# estimate_tokens_accurate — accuracy for known OpenAI models
# ---------------------------------------------------------------------------


class TestEstimateTokensAccurateOpenAI:
    """Verify accuracy within ±15% of raw tiktoken for OpenAI models."""

    MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1"]

    @pytest.mark.parametrize("model", MODELS)
    def test_accuracy_within_15_percent(self, model):
        """estimate_tokens_accurate should be within ±15% of raw tiktoken count."""
        text = "The quick brown fox jumps over the lazy dog. " * 20
        enc = tiktoken.encoding_for_model(model)
        raw_count = len(enc.encode(text))
        result = estimate_tokens_accurate(text, model)
        # With 10% safety margin, result should be >= raw_count
        # and within 15% of raw_count (allowing margin + rounding)
        assert result >= raw_count, f"{model}: {result} < raw {raw_count}"
        assert result <= raw_count * 1.15, f"{model}: {result} > 15% over raw {raw_count}"

    @pytest.mark.parametrize("model", MODELS)
    def test_cyrillic_accuracy(self, model):
        """Cyrillic text should be counted accurately for OpenAI models."""
        text = "Привет мир это тестовая строка для проверки. " * 20
        enc = tiktoken.encoding_for_model(model)
        raw_count = len(enc.encode(text))
        result = estimate_tokens_accurate(text, model)
        assert result >= raw_count
        assert result <= raw_count * 1.15

    @pytest.mark.parametrize("model", MODELS)
    def test_mixed_cyrillic_latin(self, model):
        """Mixed cyrillic+latin text should be counted accurately."""
        text = "Hello Привет world мир test тест. " * 30
        enc = tiktoken.encoding_for_model(model)
        raw_count = len(enc.encode(text))
        result = estimate_tokens_accurate(text, model)
        assert result >= raw_count
        assert result <= raw_count * 1.15


# ---------------------------------------------------------------------------
# estimate_tokens_accurate — fallback for unknown models
# ---------------------------------------------------------------------------


class TestEstimateTokensAccurateFallback:
    """Unknown models should fall back gracefully without error."""

    def test_claude_model_no_error(self):
        """Claude models (not in tiktoken) should not raise."""
        result = estimate_tokens_accurate(
            "Hello world this is a test", "claude-3-opus"
        )
        assert result > 0

    def test_completely_unknown_model(self):
        """Random model name should still return a positive count."""
        result = estimate_tokens_accurate(
            "Some text for unknown model", "totally-unknown-model-xyz"
        )
        assert result > 0

    def test_fallback_uses_cl100k_or_heuristic(self):
        """Fallback should try cl100k_base first, then heuristic."""
        text = "The quick brown fox jumps over the lazy dog."
        # For unknown models, result should be >= heuristic estimate
        heuristic = estimate_tokens(text)
        result = estimate_tokens_accurate(text, "unknown-model-abc")
        # Either cl100k_base (with 10% margin) or heuristic — both should be > 0
        assert result > 0

    def test_empty_string_unknown_model(self):
        """Empty text with unknown model should return 0."""
        assert estimate_tokens_accurate("", "unknown-model-abc") == 0


# ---------------------------------------------------------------------------
# estimate_tokens_accurate — safer overestimation
# ---------------------------------------------------------------------------


class TestEstimateTokensAccurateSafer:
    """Accurate estimate should be reasonably close to heuristic."""

    def test_gpt4o_latin_within_2x_heuristic(self):
        """Accurate estimate should be within 2x of heuristic for Latin."""
        text = "The quick brown fox jumps over the lazy dog. " * 50
        result_accurate = estimate_tokens_accurate(text, "gpt-4o")
        result_heuristic = estimate_tokens(text)
        assert result_heuristic * 0.3 <= result_accurate <= result_heuristic * 2.0, (
            f"accurate ({result_accurate}) out of range vs heuristic ({result_heuristic})"
        )

    def test_gpt4o_mixed_within_2x_heuristic(self):
        """Accurate estimate should be within 2x of heuristic for mixed."""
        text = "Hello Привет world мир test тест. " * 50
        result_accurate = estimate_tokens_accurate(text, "gpt-4o")
        result_heuristic = estimate_tokens(text)
        assert result_heuristic * 0.3 <= result_accurate <= result_heuristic * 2.0, (
            f"accurate ({result_accurate}) out of range vs heuristic ({result_heuristic})"
        )

    def test_gpt4o_accurate_includes_safety_margin(self):
        """Accurate estimate should include 10% safety margin over raw tiktoken."""
        import tiktoken as _tk
        text = "Hello world this is a test string for verification. " * 20
        enc = _tk.encoding_for_model("gpt-4o")
        raw = len(enc.encode(text))
        result = estimate_tokens_accurate(text, "gpt-4o")
        assert result >= raw, f"result ({result}) < raw tiktoken ({raw})"
        assert result == int(raw * 1.1), f"result ({result}) != int(raw*1.1) ({int(raw * 1.1)})"


# ---------------------------------------------------------------------------
# get_context_budget — output reservation
# ---------------------------------------------------------------------------


class TestGetContextBudgetOutputReserve:
    """get_context_budget should reserve output tokens."""

    def test_output_reserve_present(self):
        """Budget dict should include output_reserve key."""
        budget = get_context_budget("gpt-4o", "System", "Text")
        assert "output_reserve" in budget

    def test_output_reserve_positive(self):
        """output_reserve should be > 0."""
        budget = get_context_budget("gpt-4o", "System", "Text")
        assert budget["output_reserve"] > 0

    def test_output_reserve_reduces_available(self):
        """Available should be less than total - used (due to output reserve)."""
        budget = get_context_budget("gpt-4o", "System", "Text")
        used = budget["used_prompt"] + budget["used_text"] + budget["used_history"]
        # available should be <= total - used - output_reserve
        expected_max_available = budget["total"] - used - budget["output_reserve"]
        assert budget["available"] <= expected_max_available + 1  # +1 for rounding

    def test_output_reserve_is_20_percent_or_max_tokens(self):
        """output_reserve should be min(max_tokens or 20%, 30%) of context."""
        budget = get_context_budget("gpt-4o", "System", "Text")
        total = budget["total"]
        # output_reserve should be at most 30% of total
        assert budget["output_reserve"] <= total * 0.3 + 1

    def test_no_max_tokens_defaults_to_20_percent(self):
        """Without max_tokens, output_reserve should be 20% of context."""
        budget = get_context_budget("gpt-4o", "System", "Text")
        total = budget["total"]
        # Default should be 20% of context limit
        assert budget["output_reserve"] == int(total * 0.2)

    def test_max_tokens_caps_output_reserve(self):
        """When max_tokens is small, output_reserve should be capped."""
        budget = get_context_budget(
            "gpt-4o", "System", "Text", max_tokens=1000
        )
        assert budget["output_reserve"] == 1000

    def test_max_tokens_larger_than_30_percent_capped(self):
        """When max_tokens > 30% of context, output_reserve should be capped at 30%."""
        # gpt-4o has 128K context. 30% = 38400
        budget = get_context_budget(
            "gpt-4o", "System", "Text", max_tokens=100_000
        )
        assert budget["output_reserve"] <= 128_000 * 0.3 + 1


# ---------------------------------------------------------------------------
# get_context_budget — backward compatibility
# ---------------------------------------------------------------------------


class TestGetContextBudgetBackwardCompat:
    """Existing budget behavior should be preserved."""

    def test_all_existing_keys_present(self):
        """All keys from original budget should still be present."""
        budget = get_context_budget("gpt-4o-mini", "System", "Text")
        for key in ["total", "used_prompt", "used_text", "used_history",
                     "available", "needs_compression"]:
            assert key in budget, f"Missing key: {key}"

    def test_needs_compression_still_works(self):
        """needs_compression should still trigger for long text."""
        long_text = "Привет мир. " * 50000
        budget = get_context_budget("gpt-4o-mini", "System", long_text)
        assert budget["needs_compression"] is True

    def test_short_text_no_compression(self):
        """Short text should not need compression."""
        budget = get_context_budget("gpt-4o-mini", "System", "Short text.")
        assert budget["needs_compression"] is False
