"""Unit tests for gigaam_transcriber/chat.py."""

from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.chat import chat_with_transcript
from gigaam_transcriber.context_utils import estimate_tokens


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        result = estimate_tokens("Hello world, this is a test.")
        assert result > 0

    def test_cyrillic_text(self):
        text = "Привет мир, это тестовая строка."
        result = estimate_tokens(text)
        assert result > 0

    def test_mixed_text(self):
        text = "Hello Привет"
        result = estimate_tokens(text)
        assert result > 0


class TestTruncateHistory:
    def test_no_truncation_needed(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "gpt-4.1"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        messages = [{"role": "user", "content": "Hello"}]
        result = chat_with_transcript(text="Short text", messages=messages, llm_client=mock_client)
        assert "answer" in result

    def test_empty_messages(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "gpt-4.1"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        result = chat_with_transcript(text="Some text", messages=[], llm_client=mock_client)
        assert "answer" in result

    def test_max_10_messages(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "gpt-4.1"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        messages.append({"role": "user", "content": "final question"})
        result = chat_with_transcript(text="short", messages=messages, llm_client=mock_client)

        call_args = mock_client._get_client.return_value.chat.completions.create.call_args
        sent_messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        history_msgs = [m for m in sent_messages if m["role"] in ("user", "assistant")]
        assert len(history_msgs) <= 11


class TestChatWithTranscript:
    def test_basic_chat(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is the answer."
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "test-model"
        mock_client.config.api_key = "test-key"
        mock_client.config.base_url = "http://test"

        result = chat_with_transcript(
            text="Speaker 1: Hello world.",
            messages=[{"role": "user", "content": "What was said?"}],
            llm_client=mock_client,
        )

        assert result == {"answer": "This is the answer."}
        mock_client._get_client.return_value.chat.completions.create.assert_called_once()

    def test_chat_with_model_override(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "model-a"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        chat_with_transcript(
            text="Some text",
            messages=[{"role": "user", "content": "Question"}],
            model="model-b",
            llm_client=mock_client,
        )

        mock_client.update_config.assert_not_called()
        create_call = mock_client._get_client.return_value.chat.completions.create.call_args
        assert create_call.kwargs["model"] == "model-b"

    def test_chat_with_history(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Follow-up answer"
        mock_client._get_client.return_value.chat.completions.create.return_value = mock_response
        mock_client.config.model = "test-model"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up question"},
        ]

        result = chat_with_transcript(
            text="Transcript text",
            messages=messages,
            llm_client=mock_client,
        )

        assert result["answer"] == "Follow-up answer"
        call_args = mock_client._get_client.return_value.chat.completions.create.call_args
        sent_messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert len(sent_messages) == 2 + 3

    def test_chat_error_handling(self):
        mock_client = MagicMock()
        mock_client._get_client.return_value.chat.completions.create.side_effect = RuntimeError("API error")
        mock_client.config.model = "test-model"
        mock_client.config.api_key = "key"
        mock_client.config.base_url = "http://test"

        with pytest.raises(RuntimeError, match="API error"):
            chat_with_transcript(
                text="Some text",
                messages=[{"role": "user", "content": "Question"}],
                llm_client=mock_client,
            )

    def test_chat_lazy_client_init(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer"

        with patch("gigaam_transcriber.chat.LLMClient") as MockLLM:
            mock_instance = MockLLM.return_value
            mock_instance._get_client.return_value.chat.completions.create.return_value = mock_response
            mock_instance.config.model = "test"
            mock_instance.config.api_key = "key"
            mock_instance.config.base_url = "http://test"

            result = chat_with_transcript(
                text="Text",
                messages=[{"role": "user", "content": "Q"}],
            )

        assert result["answer"] == "Answer"


# ---------------------------------------------------------------------------
# Bounded chunk-summary cache (Task 14): size cap, TTL, isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_chunk_summary_cache():
    from gigaam_transcriber.chat import reset_chunk_summary_cache

    reset_chunk_summary_cache()
    yield
    reset_chunk_summary_cache()


class TestBoundedChunkSummaryCache:
    def _cache_helpers(self):
        from gigaam_transcriber import chat as chat_module
        from gigaam_transcriber.chat import _cache_get, _cache_put

        return chat_module, _cache_get, _cache_put

    def test_cache_size_bound_fifo_eviction(self, monkeypatch):
        chat_module, _cache_get, _cache_put = self._cache_helpers()
        monkeypatch.setenv("CHAT_CACHE_MAX_ENTRIES", "3")
        for i in range(5):
            _cache_put(f"key-{i}", [{"index": i}])

        assert len(chat_module._chunk_summary_cache) == 3
        assert _cache_get("key-0") is None
        assert _cache_get("key-1") is None
        assert _cache_get("key-2") == [{"index": 2}]
        assert _cache_get("key-3") == [{"index": 3}]
        assert _cache_get("key-4") == [{"index": 4}]

    def test_cache_ttl_expiry(self, monkeypatch):
        import time

        chat_module, _cache_get, _cache_put = self._cache_helpers()
        monkeypatch.setenv("CHAT_CACHE_TTL_SECONDS", "0.05")
        _cache_put("key", [{"index": 0}])
        assert _cache_get("key") == [{"index": 0}]
        time.sleep(0.1)
        assert _cache_get("key") is None
        assert "key" not in chat_module._chunk_summary_cache

    def test_cache_reinsert_refreshes_fifo_order(self, monkeypatch):
        _, _cache_get, _cache_put = self._cache_helpers()
        monkeypatch.setenv("CHAT_CACHE_MAX_ENTRIES", "2")
        _cache_put("a", [{"index": 0}])
        _cache_put("b", [{"index": 1}])
        _cache_get("a")
        _cache_put("a", [{"index": 0}])  # re-touched → moved to the back
        _cache_put("c", [{"index": 2}])  # evicts "b", not "a"
        assert _cache_get("a") == [{"index": 0}]
        assert _cache_get("b") is None
        assert _cache_get("c") == [{"index": 2}]

    def test_compression_cache_hit_and_ttl_miss_via_chat_flow(self, monkeypatch):
        import time

        from gigaam_transcriber.chat import _get_compressed_transcript

        monkeypatch.setenv("CHAT_CACHE_TTL_SECONDS", "0.1")
        llm = MagicMock()
        llm.call.return_value = "Часть: суть фрагмента"
        text = "Спикер 1: короткий текст транскрипции."
        budget = {"total": 128000}

        first = _get_compressed_transcript(text, None, llm, budget)
        assert llm.call.call_count == 1
        second = _get_compressed_transcript(text, None, llm, budget)
        assert llm.call.call_count == 1, "cache hit must not re-summarize"
        assert first == second

        time.sleep(0.15)
        _get_compressed_transcript(text, None, llm, budget)
        assert llm.call.call_count == 2, "expired entry must trigger re-summarization"

    def test_no_cross_text_data_leakage(self):
        from gigaam_transcriber.chat import _get_compressed_transcript

        llm = MagicMock()
        llm.call.side_effect = ["сводка A", "сводка B"]
        text_a = "Транскрипт пользователя A: продажи."
        text_b = "Транскрипт пользователя B: поддержка."

        ctx_a = _get_compressed_transcript(text_a, None, llm, {"total": 128000})
        ctx_b = _get_compressed_transcript(text_b, None, llm, {"total": 128000})

        assert "сводка A" in ctx_a and "сводка B" not in ctx_a
        assert "сводка B" in ctx_b and "сводка A" not in ctx_b
        assert llm.call.call_count == 2

    def test_concurrent_cache_access_thread_safe(self, monkeypatch):
        import threading

        chat_module, _cache_get, _cache_put = self._cache_helpers()
        monkeypatch.setenv("CHAT_CACHE_MAX_ENTRIES", "8")
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                for i in range(50):
                    key = f"k-{idx}-{i % 12}"
                    _cache_put(key, [{"index": i}])
                    _cache_get(key)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert len(chat_module._chunk_summary_cache) <= 8
