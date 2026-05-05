"""E2E tests for long-context support: map-reduce for insights/mindmap, compressed chat."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.chat import (
    chat_with_transcript,
    _chunk_summary_cache,
    _create_chunk_summaries,
    _build_compressed_context,
)
from gigaam_transcriber.context_utils import estimate_tokens
from gigaam_transcriber.insights import extract_action_items, generate_suggested_steps
from gigaam_transcriber.mindmap import generate_mindmap_markdown
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig


def _make_long_text(n_sentences=1200):
    return ". ".join(
        [
            f"Спикер номер {i} сказал что проект нужно завершить к концу месяца "
            f"и назначить ответственных за каждый этап выполнения задач"
            for i in range(n_sentences)
        ]
    )


def _make_llm_client(responses=None):
    config = LLMClientConfig(api_key="sk-test", model="gpt-4o-mini")
    client = LLMClient(config)
    mock_openai = MagicMock()

    if responses:
        call_idx = [0]

        def side_effect(**kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if idx < len(responses):
                resp.choices[0].message.content = responses[idx]
            else:
                resp.choices[0].message.content = responses[-1]
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect
    else:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "default response"
        mock_openai.chat.completions.create.return_value = resp

    client._client = mock_openai
    return client


LONG_TEXT = _make_long_text()


class TestInsightsMapReduce:
    def test_extract_action_items_long_text(self):
        chunk_json = json.dumps({
            "action_items": [{"task": "Complete project", "priority": "high"}],
            "decisions": [],
        })
        reduced_json = json.dumps({
            "action_items": [
                {"task": "Complete project", "priority": "high"},
            ],
            "decisions": [],
        })
        client = _make_llm_client(responses=[chunk_json, reduced_json])

        result = extract_action_items(LONG_TEXT, client)
        assert "action_items" in result
        assert len(result["action_items"]) >= 1
        assert client._client.chat.completions.create.call_count >= 2

    def test_extract_action_items_short_text_single_call(self):
        short_text = "Короткий текст для тестирования."
        single_json = json.dumps({
            "action_items": [{"task": "Do something", "priority": "medium"}],
            "decisions": [],
        })
        client = _make_llm_client(responses=[single_json])

        result = extract_action_items(short_text, client)
        assert "action_items" in result
        assert client._client.chat.completions.create.call_count == 1

    def test_generate_suggested_steps_long_text(self):
        chunk_json = json.dumps({
            "suggested_steps": [
                {"step": "Review plan", "reason": "important", "category": "followup"}
            ]
        })
        reduced_json = json.dumps({
            "suggested_steps": [
                {"step": "Review plan", "reason": "important", "category": "followup"},
            ]
        })
        client = _make_llm_client(responses=[chunk_json, reduced_json])

        result = generate_suggested_steps(LONG_TEXT, client)
        assert "suggested_steps" in result
        assert client._client.chat.completions.create.call_count >= 2


class TestMindmapMapReduce:
    def test_mindmap_long_text(self):
        subtree = "# Тема\n## Подтема 1\n- detail 1\n## Подтема 2\n- detail 2"
        merged = "# Общая тема\n## Раздел 1\n- пункт 1\n## Раздел 2\n- пункт 2"
        client = _make_llm_client(responses=[subtree, merged])

        result = generate_mindmap_markdown(LONG_TEXT, client)
        assert "# " in result
        assert "## " in result
        assert client._client.chat.completions.create.call_count >= 2

    def test_mindmap_short_text_single_call(self):
        short_text = "Короткая транскрипция для теста."
        single_result = "# Тема\n## Подтема\n- detail"
        client = _make_llm_client(responses=[single_result])

        result = generate_mindmap_markdown(short_text, client)
        assert "# " in result
        assert client._client.chat.completions.create.call_count == 1


class TestChatLongContext:
    def setup_method(self):
        _chunk_summary_cache.clear()

    def test_chat_with_long_transcript(self):
        client = _make_llm_client(
            responses=["Chunk summary text", "Основной ответ на вопрос пользователя."]
        )

        result = chat_with_transcript(
            text=LONG_TEXT,
            messages=[{"role": "user", "content": "Какие задачи нужно выполнить?"}],
            llm_client=client,
        )

        assert "answer" in result
        assert len(result["answer"]) > 0
        assert client._client.chat.completions.create.call_count >= 2

    def test_chunk_summary_cache_hit(self):
        client = _make_llm_client(
            responses=["Summary chunk", "First answer", "Second answer"]
        )

        chat_with_transcript(
            text=LONG_TEXT,
            messages=[{"role": "user", "content": "Question 1"}],
            llm_client=client,
        )

        first_call_count = client._client.chat.completions.create.call_count

        chat_with_transcript(
            text=LONG_TEXT,
            messages=[
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Question 2"},
            ],
            llm_client=client,
        )

        second_call_count = client._client.chat.completions.create.call_count
        assert second_call_count - first_call_count == 1

    def test_chat_short_transcript_no_compression(self):
        short_text = "Спикер 1: Привет, как дела? Спикер 2: Всё отлично."
        client = _make_llm_client(responses=["Ответ на вопрос."])

        result = chat_with_transcript(
            text=short_text,
            messages=[{"role": "user", "content": "Что было сказано?"}],
            llm_client=client,
        )

        assert result["answer"] == "Ответ на вопрос."
        assert client._client.chat.completions.create.call_count == 1

    def test_chat_multiple_questions_history_preserved(self):
        client = _make_llm_client(
            responses=["Summary text", "Answer 1", "Answer 2"]
        )

        chat_with_transcript(
            text=LONG_TEXT,
            messages=[{"role": "user", "content": "Question 1"}],
            llm_client=client,
        )

        chat_with_transcript(
            text=LONG_TEXT,
            messages=[
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
            ],
            llm_client=client,
        )

        last_call = client._client.chat.completions.create.call_args_list[-1]
        sent_messages = last_call.kwargs.get("messages") or last_call[1].get("messages")
        history_msgs = [m for m in sent_messages if m["role"] in ("user", "assistant")]
        assert len(history_msgs) >= 2


class TestCreateChunkSummaries:
    def test_creates_summaries(self):
        client = _make_llm_client(responses=["Summary 1", "Summary 2", "Summary 3"])

        text = ". ".join([f"Предложение номер {i} для тестирования чанков" for i in range(200)])
        summaries = _create_chunk_summaries(text, client, chunk_size=500)

        assert len(summaries) >= 1
        assert all("summary" in s for s in summaries)
        assert all("chunk_text" in s for s in summaries)

    def test_handles_llm_failure_gracefully(self):
        client = _make_llm_client()
        client._client.chat.completions.create.side_effect = RuntimeError("API error")

        text = ". ".join([f"Предложение номер {i}" for i in range(100)])
        summaries = _create_chunk_summaries(text, client, chunk_size=200)

        assert len(summaries) >= 1
        for s in summaries:
            assert "summary" in s
            assert len(s["summary"]) > 0


class TestBuildCompressedContext:
    def test_includes_overview_and_relevant_chunks(self):
        summaries = [
            {"index": 0, "summary": "Discussing project deadlines", "chunk_text": "Full text of chunk 0 about deadlines"},
            {"index": 1, "summary": "Budget allocation discussion", "chunk_text": "Full text of chunk 1 about budget"},
            {"index": 2, "summary": "Team assignments", "chunk_text": "Full text of chunk 2 about team"},
        ]

        context = _build_compressed_context(summaries, "deadlines budget", max_tokens=50000)

        assert "СЖАТЫЙ КОНТЕКСТ" in context
        assert "РЕЛЕВАНТНЫЕ ФРАГМЕНТЫ" in context

    def test_no_query_skips_relevant_chunks(self):
        summaries = [
            {"index": 0, "summary": "Summary text", "chunk_text": "Chunk text"},
        ]

        context = _build_compressed_context(summaries, "", max_tokens=50000)

        assert "СЖАТЫЙ КОНТЕКСТ" in context
        assert "РЕЛЕВАНТНЫЕ" not in context
