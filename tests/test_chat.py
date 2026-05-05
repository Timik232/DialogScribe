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

        mock_client.update_config.assert_called_once()

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
