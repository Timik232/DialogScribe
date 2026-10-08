"""Tests for structured logging in LLMClient.call()."""

import logging
import pytest
from unittest.mock import MagicMock

from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig


LOGGER_NAME = "gigaam_transcriber.llm"


def _make_client(model="gpt-4.1", api_key="sk-test"):
    config = LLMClientConfig(api_key=api_key, model=model)
    return LLMClient(config)


def _mock_response(content="Test summary", prompt_tokens=100, completion_tokens=50):
    """Create a mock OpenAI response with usage data."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = prompt_tokens
    resp.usage.completion_tokens = completion_tokens
    resp.usage.total_tokens = prompt_tokens + completion_tokens
    return resp


class TestLoggerName:
    """Verify the logger uses the correct name."""

    def test_logger_name_is_gigaam_transcriber_llm(self):
        import gigaam_transcriber.summarizer as mod

        assert mod.logger.name == LOGGER_NAME


class TestBeforeCallLogging:
    """Tests for the BEFORE-call INFO log."""

    def test_before_call_logs_model_name(self, caplog):
        client = _make_client(model="gpt-4.1-test")
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        before_logs = [r for r in caplog.records if "LLM call starting" in r.message]
        assert len(before_logs) >= 1
        assert "gpt-4.1-test" in before_logs[0].message

    def test_before_call_logs_message_count(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        before_logs = [r for r in caplog.records if "LLM call starting" in r.message]
        assert len(before_logs) >= 1
        # 2 messages: system + user
        assert "messages=2" in before_logs[0].message

    def test_before_call_logs_estimated_input_tokens(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        before_logs = [r for r in caplog.records if "LLM call starting" in r.message]
        assert len(before_logs) >= 1
        assert "estimated_input_tokens=" in before_logs[0].message

    def test_before_call_at_info_level(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        before_logs = [r for r in caplog.records if "LLM call starting" in r.message]
        assert all(r.levelno == logging.INFO for r in before_logs)


class TestSuccessLogging:
    """Tests for the AFTER-success INFO log."""

    def test_success_logs_input_tokens(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response(
            prompt_tokens=150, completion_tokens=80
        )
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert len(success_logs) >= 1
        assert "input_tokens=150" in success_logs[0].message

    def test_success_logs_output_tokens(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response(
            prompt_tokens=150, completion_tokens=80
        )
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert len(success_logs) >= 1
        assert "output_tokens=80" in success_logs[0].message

    def test_success_logs_latency_ms(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert len(success_logs) >= 1
        assert "latency_ms=" in success_logs[0].message

    def test_success_logs_status_success(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert len(success_logs) >= 1
        assert "status=success" in success_logs[0].message

    def test_success_logs_model_name(self, caplog):
        client = _make_client(model="gpt-4o-mini")
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert len(success_logs) >= 1
        assert "gpt-4o-mini" in success_logs[0].message

    def test_success_at_info_level(self, caplog):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response()
        client._client = mock_openai

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client.call("system prompt", "user text")

        success_logs = [r for r in caplog.records if "LLM call completed" in r.message]
        assert all(r.levelno == logging.INFO for r in success_logs)


class TestErrorLogging:
    """Tests for the ON-error ERROR log."""

    def test_error_logs_error_type(self, caplog):
        from openai import AuthenticationError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            with pytest.raises(ValueError):
                client.call("system", "user")

        error_logs = [r for r in caplog.records if "LLM call failed" in r.message]
        assert len(error_logs) >= 1
        assert "AuthenticationError" in error_logs[0].message

    def test_error_logs_latency_ms(self, caplog):
        from openai import RateLimitError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = RateLimitError(
            message="Rate limited", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            with pytest.raises(RuntimeError):
                client.call("system", "user")

        error_logs = [r for r in caplog.records if "LLM call failed" in r.message]
        assert len(error_logs) >= 1
        assert "latency_ms=" in error_logs[0].message

    def test_error_logs_status_error(self, caplog):
        from openai import APIConnectionError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock()
        )
        client._client = mock_openai

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            with pytest.raises(ConnectionError):
                client.call("system", "user")

        error_logs = [r for r in caplog.records if "LLM call failed" in r.message]
        assert len(error_logs) >= 1
        assert "status=error" in error_logs[0].message

    def test_error_logs_model_name(self, caplog):
        from openai import APIError

        client = _make_client(model="gpt-4.1")
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = APIError(
            message="Server error", request=MagicMock(), body=None
        )
        client._client = mock_openai

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            with pytest.raises(RuntimeError):
                client.call("system", "user")

        error_logs = [r for r in caplog.records if "LLM call failed" in r.message]
        assert len(error_logs) >= 1
        assert "gpt-4.1" in error_logs[0].message

    def test_error_at_error_level(self, caplog):
        from openai import AuthenticationError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            with pytest.raises(ValueError):
                client.call("system", "user")

        error_logs = [r for r in caplog.records if "LLM call failed" in r.message]
        assert all(r.levelno == logging.ERROR for r in error_logs)


class TestErrorHandlingPreserved:
    """Verify existing error handling is preserved after logging changes."""

    def test_auth_error_still_raises_valueerror(self):
        from openai import AuthenticationError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with pytest.raises(ValueError, match="авторизации"):
            client.call("system", "user")

    def test_rate_limit_still_raises_runtimeerror(self):
        from openai import RateLimitError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = RateLimitError(
            message="Rate limited", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with pytest.raises(RuntimeError, match="лимит"):
            client.call("system", "user")

    def test_connection_error_still_raises_connectionerror(self):
        from openai import APIConnectionError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock()
        )
        client._client = mock_openai

        with pytest.raises(ConnectionError):
            client.call("system", "user")

    def test_api_error_still_raises_runtimeerror(self):
        from openai import APIError

        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = APIError(
            message="Server error", request=MagicMock(), body=None
        )
        client._client = mock_openai

        with pytest.raises(RuntimeError, match="Ошибка API"):
            client.call("system", "user")

    def test_success_return_value_preserved(self):
        client = _make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response(
            content="## Custom Summary\n- Point 1"
        )
        client._client = mock_openai

        result = client.call("system prompt", "user text")
        assert result == "## Custom Summary\n- Point 1"
