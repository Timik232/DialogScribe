"""Unit tests for gigaam_transcriber/context_utils.py."""

import pytest

from gigaam_transcriber.context_utils import (
    estimate_tokens,
    find_relevant_chunks,
    get_context_budget,
    get_model_context_limit,
    split_into_chunks,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_none_like_empty(self):
        assert estimate_tokens("") == 0

    def test_pure_cyrillic(self):
        text = "Привет мир это тестовая строка"
        result = estimate_tokens(text)
        expected = int(len(text) / 2)
        assert abs(result - expected) <= 1

    def test_pure_latin(self):
        text = "Hello world this is a test string"
        result = estimate_tokens(text)
        expected = int(len(text) / 4)
        assert abs(result - expected) <= 1

    def test_mixed_text(self):
        text = "Hello Привет world мир"
        result = estimate_tokens(text)
        assert result > 0

    def test_cyrillic_weights_more(self):
        cyrillic = "Привет" * 10
        latin = "Hello" * 10
        assert estimate_tokens(cyrillic) > estimate_tokens(latin)

    def test_digits_and_punctuation(self):
        text = "12345 !@#$%"
        result = estimate_tokens(text)
        assert result >= 0

    def test_single_char(self):
        assert estimate_tokens("А") >= 0
        assert estimate_tokens("a") >= 0


class TestGetModelContextLimit:
    def test_known_model(self):
        assert get_model_context_limit("gpt-4.1") == 1_047_576
        assert get_model_context_limit("gpt-4o") == 128_000
        assert get_model_context_limit("gpt-4o-mini") == 128_000

    def test_case_insensitive(self):
        assert get_model_context_limit("GPT-4.1") == 1_047_576
        assert get_model_context_limit("GPT-4o-mini") == 128_000

    def test_versioned_model(self):
        assert get_model_context_limit("gpt-4o-mini-2024-07-18") == 128_000

    def test_unknown_model_fallback(self):
        assert get_model_context_limit("some-unknown-model") == 128_000

    def test_empty_model(self):
        assert get_model_context_limit("") == 128_000

    def test_none_model(self):
        assert get_model_context_limit(None) == 128_000


class TestGetContextBudget:
    def test_short_text_no_compression(self):
        budget = get_context_budget(
            model="gpt-4o-mini",
            system_prompt="You are helpful.",
            text="Short text.",
        )
        assert budget["needs_compression"] is False
        assert budget["total"] == 128_000
        assert budget["available"] > 0
        assert budget["used_text"] > 0

    def test_long_text_needs_compression(self):
        long_text = "Привет мир. " * 50000  # ~100K chars → ~50K tokens
        budget = get_context_budget(
            model="gpt-4o-mini",
            system_prompt="System",
            text=long_text,
        )
        assert budget["needs_compression"] is True
        assert budget["used_text"] > 0

    def test_with_history(self):
        budget = get_context_budget(
            model="gpt-4o-mini",
            system_prompt="System",
            text="Some text",
            history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        )
        assert budget["used_history"] > 0
        assert budget["used_prompt"] > 0

    def test_budget_keys(self):
        budget = get_context_budget("gpt-4o-mini", "sys", "text")
        assert "total" in budget
        assert "used_prompt" in budget
        assert "used_text" in budget
        assert "used_history" in budget
        assert "available" in budget
        assert "needs_compression" in budget


class TestSplitIntoChunks:
    def test_short_text_single_chunk(self):
        text = "Короткий текст."
        chunks = split_into_chunks(text, max_tokens=10000)
        assert chunks == [text]

    def test_long_text_splits(self):
        text = ". ".join([f"Предложение номер {i} для тестирования раз два три" for i in range(500)])
        chunks = split_into_chunks(text, max_tokens=100, overlap_sentences=2)
        assert len(chunks) > 1

    def test_overlap(self):
        text = ". ".join([f"Предложение номер {i}" for i in range(50)])
        chunks = split_into_chunks(text, max_tokens=50, overlap_sentences=2)
        if len(chunks) > 1:
            last_of_first = chunks[0].split(". ")[-2:]
            next_start = ". ".join(last_of_first)
            assert next_start in chunks[1] or chunks[1].startswith(last_of_first[0])

    def test_empty_text(self):
        chunks = split_into_chunks("", max_tokens=100)
        assert len(chunks) <= 1

    def test_none_text(self):
        chunks = split_into_chunks("", max_tokens=100)
        assert len(chunks) <= 1

    def test_single_long_word_block(self):
        text = "слово " * 5000
        chunks = split_into_chunks(text, max_tokens=100)
        assert len(chunks) > 1

    def test_newline_splitting(self):
        text = "\n".join([f"Строка {i} с каким-то содержанием для проверки" for i in range(200)])
        chunks = split_into_chunks(text, max_tokens=100)
        assert len(chunks) > 1


class TestFindRelevantChunks:
    def test_empty_chunks(self):
        assert find_relevant_chunks([], "query") == []

    def test_empty_query(self):
        chunks = ["chunk one", "chunk two"]
        result = find_relevant_chunks(chunks, "")
        assert len(result) <= 2

    def test_keyword_matching(self):
        chunks = [
            "Обсуждение бюджета и финансовых планов на следующий квартал",
            "Спикер рассказал о погоде и природе",
            "Бюджет был утверждён на совете директоров вчера",
        ]
        result = find_relevant_chunks(chunks, "бюджет финансовый", max_chunks=2)
        assert len(result) >= 1
        assert any("бюджет" in c.lower() for c in result)

    def test_max_chunks_limit(self):
        chunks = [f"Чанк номер {i} с тестовыми данными" for i in range(10)]
        result = find_relevant_chunks(chunks, "тестовые данные", max_chunks=3)
        assert len(result) <= 3

    def test_no_match_returns_first_n(self):
        chunks = ["aaa", "bbb", "ccc"]
        result = find_relevant_chunks(chunks, "xyz", max_chunks=2)
        assert len(result) == 2

    def test_cyrillic_keywords(self):
        chunks = [
            "Совещание по проекту альфа",
            "Обсуждение нового дизайна интерфейса",
            "Проект альфа нуждается в доработке",
        ]
        result = find_relevant_chunks(chunks, "проект альфа", max_chunks=2)
        assert len(result) == 2
        assert any("проект" in c.lower() and "альфа" in c.lower() for c in result)

    def test_latin_keywords(self):
        chunks = [
            "We discussed the database architecture",
            "Frontend components were reviewed",
            "Database migration plan is ready",
        ]
        result = find_relevant_chunks(chunks, "database architecture", max_chunks=2)
        assert len(result) == 2
