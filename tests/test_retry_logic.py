"""TDD tests for retry logic with exponential backoff in LLMClient.call()."""

import time
from unittest.mock import MagicMock, patch

import pytest
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig


def _make_client(api_key="sk-test", max_retries=3):
    config = LLMClientConfig(api_key=api_key, max_retries=max_retries)
    return LLMClient(config)


def _mock_response(content="Test summary"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _api_error(status_code=500, message="Server error"):
    """Create an APIError with a specific status_code."""
    resp = MagicMock()
    resp.status_code = status_code
    err = APIError(message=message, request=MagicMock(), body=None)
    err.status_code = status_code
    return err


# ===== Config tests =====


class TestLLMClientConfigMaxRetries:
    def test_default_max_retries(self):
        config = LLMClientConfig()
        assert config.max_retries == 3

    def test_custom_max_retries(self):
        config = LLMClientConfig(max_retries=5)
        assert config.max_retries == 5

    def test_zero_max_retries(self):
        config = LLMClientConfig(max_retries=0)
        assert config.max_retries == 0


# ===== Retry on transient errors =====


class TestRetryOnTransientErrors:
    def test_retry_on_rate_limit_error(self):
        """RateLimitError is retried and eventually succeeds."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        # Fail twice with RateLimitError, then succeed
        mock_openai.chat.completions.create.side_effect = [
            RateLimitError(message="rate limited", response=MagicMock(), body=None),
            RateLimitError(message="rate limited", response=MagicMock(), body=None),
            _mock_response("Success after retry"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            result = client.call("system", "user")

        assert result == "Success after retry"
        assert mock_openai.chat.completions.create.call_count == 3
        # Sleep called twice (after 1st and 2nd failure), not after success
        assert mock_sleep.call_count == 2

    def test_retry_on_api_connection_error(self):
        """APIConnectionError is retried and eventually succeeds."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            APIConnectionError(request=MagicMock()),
            _mock_response("Connected"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            result = client.call("system", "user")

        assert result == "Connected"
        assert mock_openai.chat.completions.create.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retry_on_api_error_500(self):
        """APIError with status >= 500 is retried."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            _api_error(500, "Internal Server Error"),
            _mock_response("OK"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            result = client.call("system", "user")

        assert result == "OK"
        assert mock_openai.chat.completions.create.call_count == 2

    def test_retry_on_api_error_502(self):
        """APIError with status 502 is retried."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            _api_error(502, "Bad Gateway"),
            _mock_response("OK"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            result = client.call("system", "user")

        assert result == "OK"

    def test_retry_on_api_error_503(self):
        """APIError with status 503 is retried."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            _api_error(503, "Service Unavailable"),
            _mock_response("OK"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            result = client.call("system", "user")

        assert result == "OK"


# ===== No retry on non-transient errors =====


class TestNoRetryOnNonTransientErrors:
    def test_no_retry_on_authentication_error(self):
        """AuthenticationError is NOT retried — raises immediately."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad API key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(ValueError, match="авторизации"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    def test_no_retry_on_api_error_400(self):
        """APIError with status < 500 is NOT retried."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = _api_error(400, "Bad Request")
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="Ошибка API"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    def test_no_retry_on_api_error_404(self):
        """APIError with status 404 is NOT retried."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = _api_error(404, "Not Found")
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="Ошибка API"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    def test_no_retry_on_api_error_429(self):
        """APIError with status 429 is NOT retried (RateLimitError is a different class)."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = _api_error(429, "Too Many Requests")
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="Ошибка API"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0


# ===== Exponential backoff =====


class TestExponentialBackoff:
    def test_backoff_doubles_each_attempt(self):
        """Backoff follows 2^attempt pattern: 1s, 2s, 4s."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            RateLimitError(message="rl", response=MagicMock(), body=None),
            RateLimitError(message="rl", response=MagicMock(), body=None),
            RateLimitError(message="rl", response=MagicMock(), body=None),
            _mock_response("finally"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            result = client.call("system", "user")

        assert result == "finally"
        # attempt 0: sleep(2**0)=1, attempt 1: sleep(2**1)=2, attempt 2: sleep(2**2)=4
        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list[0][0][0] == 1  # 2**0
        assert mock_sleep.call_args_list[1][0][0] == 2  # 2**1
        assert mock_sleep.call_args_list[2][0][0] == 4  # 2**2


# ===== Retry exhaustion =====


class TestRetryExhaustion:
    def test_raises_last_error_after_all_retries_exhausted(self):
        """When all retries are exhausted, the last error is raised."""
        client = _make_client(max_retries=2)
        mock_openai = MagicMock()
        error = RateLimitError(message="still rate limited", response=MagicMock(), body=None)
        mock_openai.chat.completions.create.side_effect = [
            RateLimitError(message="rl1", response=MagicMock(), body=None),
            RateLimitError(message="rl2", response=MagicMock(), body=None),
            error,
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            with pytest.raises(RuntimeError, match="лимит"):
                client.call("system", "user")

        # 1 initial + 2 retries = 3 total attempts
        assert mock_openai.chat.completions.create.call_count == 3

    def test_raises_connection_error_after_exhaustion(self):
        """APIConnectionError exhaustion raises ConnectionError."""
        client = _make_client(max_retries=1)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            APIConnectionError(request=MagicMock()),
            APIConnectionError(request=MagicMock()),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            with pytest.raises(ConnectionError):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 2

    def test_raises_runtime_error_on_api_500_exhaustion(self):
        """APIError >= 500 exhaustion raises RuntimeError."""
        client = _make_client(max_retries=1)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            _api_error(500, "err1"),
            _api_error(500, "err2"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            with pytest.raises(RuntimeError, match="Ошибка API"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 2


# ===== Zero retries =====


class TestZeroRetries:
    def test_zero_retries_raises_immediately_on_transient_error(self):
        """max_retries=0 means no retry — fails on first error."""
        client = _make_client(max_retries=0)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = RateLimitError(
            message="rl", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="лимит"):
                client.call("system", "user")

        assert mock_openai.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    def test_zero_retries_succeeds_on_first_try(self):
        """max_retries=0 still works if first call succeeds."""
        client = _make_client(max_retries=0)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response("OK")
        client._client = mock_openai

        result = client.call("system", "user")
        assert result == "OK"
        assert mock_openai.chat.completions.create.call_count == 1


# ===== Logging =====


class TestRetryLogging:
    def test_retry_logs_warning(self):
        """Each retry attempt logs a WARNING with the correct format."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = [
            RateLimitError(message="rl", response=MagicMock(), body=None),
            _mock_response("OK"),
        ]
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.time.sleep"):
            with patch("gigaam_transcriber.summarizer.logger") as mock_logger:
                client.call("system", "user")

        # Find the retry warning call
        warning_calls = mock_logger.warning.call_args_list
        assert len(warning_calls) >= 1
        call_str = str(warning_calls[0])
        assert "retry" in call_str
        assert "attempt" in call_str

    def test_success_still_logs_info(self):
        """Successful call still logs INFO (preserving T2 logging)."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _mock_response("OK")
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.logger") as mock_logger:
            client.call("system", "user")

        info_calls = mock_logger.info.call_args_list
        # Should have at least "starting" and "completed" logs
        assert len(info_calls) >= 2

    def test_auth_error_still_logs_error(self):
        """AuthenticationError still logs ERROR (preserving T2 logging)."""
        client = _make_client(max_retries=3)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with patch("gigaam_transcriber.summarizer.logger") as mock_logger:
            with pytest.raises(ValueError):
                client.call("system", "user")

        error_calls = mock_logger.error.call_args_list
        assert len(error_calls) >= 1
