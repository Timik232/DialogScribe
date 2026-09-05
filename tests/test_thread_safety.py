"""Thread-safety tests for per-request model_override in LLMClient.call()."""

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig, DEFAULT_MODEL


def _make_client(api_key: str = "sk-test", model: str = DEFAULT_MODEL) -> LLMClient:
    config = LLMClientConfig(api_key=api_key, model=model)
    client = LLMClient(config)
    # Pre-set mock client to bypass lazy init
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_openai.chat.completions.create.return_value = mock_response
    client._client = mock_openai
    return client


class TestModelOverrideInCall:
    """model_override parameter in LLMClient.call()."""

    def test_model_override_in_call(self):
        """call() uses model_override when provided, not self._config.model."""
        client = _make_client(model="gpt-4.1")
        client.call("sys", "user", model_override="gpt-4o-mini")
        call_kwargs = client._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o-mini"

    def test_model_override_does_not_mutate_config(self):
        """After call with model_override, self._config.model is unchanged."""
        client = _make_client(model="gpt-4.1")
        assert client.config.model == "gpt-4.1"
        client.call("sys", "user", model_override="gpt-4o-mini")
        assert client.config.model == "gpt-4.1"

    def test_model_override_none_uses_default(self):
        """When model_override=None, uses self._config.model."""
        client = _make_client(model="gpt-4.1")
        client.call("sys", "user", model_override=None)
        call_kwargs = client._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4.1"

    def test_model_override_omitted_uses_default(self):
        """When model_override is not passed, uses self._config.model."""
        client = _make_client(model="gpt-4.1")
        client.call("sys", "user")
        call_kwargs = client._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4.1"

    def test_concurrent_different_models(self):
        """10 concurrent requests alternating models — verify each call got correct model."""
        client = _make_client(model="gpt-4.1")

        # Track which model each call received
        received_models: list[tuple[int, str]] = []
        original_create = client._client.chat.completions.create

        def track_create(**kwargs):
            # Thread-safe append via lock
            received_models.append((threading.current_thread().ident, kwargs["model"]))
            return original_create.return_value

        client._client.chat.completions.create.side_effect = track_create

        # Lock for thread-safe result collection
        lock = threading.Lock()
        results: dict[int, str] = {}
        errors: list[Exception] = []

        def make_call(idx: int, model: str):
            try:
                r = client.call(f"sys-{idx}", f"user-{idx}", model_override=model)
                with lock:
                    results[idx] = r
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(10):
            model = "gpt-4o-mini" if i % 2 == 0 else "gpt-4.1"
            t = threading.Thread(target=make_call, args=(i, model))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent calls: {errors}"
        assert len(received_models) == 10

        # Verify alternating pattern: even indices got gpt-4o-mini, odd got gpt-4.1
        # (We can't strictly map thread IDs back to indices, but we can check the mix)
        models_used = {m for _, m in received_models}
        assert models_used == {"gpt-4o-mini", "gpt-4.1"}

        # Verify config was NOT mutated
        assert client.config.model == "gpt-4.1"

    def test_no_update_config_during_concurrent(self):
        """Verify update_config is NOT called during concurrent LLM calls."""
        client = _make_client(model="gpt-4.1")

        update_config_calls = []
        original_update = client.update_config

        def track_update_config(*args, **kwargs):
            update_config_calls.append(threading.current_thread().ident)
            return original_update(*args, **kwargs)

        client.update_config = track_update_config

        errors: list[Exception] = []

        def make_call(idx: int):
            try:
                client.call(f"sys-{idx}", f"user-{idx}", model_override="gpt-4o-mini")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=make_call, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors: {errors}"
        assert update_config_calls == [], (
            f"update_config was called {len(update_config_calls)} times during concurrent calls"
        )

    def test_logging_shows_effective_model_with_override(self, caplog):
        """When model_override is used, logging shows the effective model."""
        client = _make_client(model="gpt-4.1")
        with caplog.at_level(logging.INFO, logger="gigaam_transcriber.llm"):
            client.call("sys", "user", model_override="gpt-4o-mini")

        start_logs = [r for r in caplog.records if "LLM call starting" in r.message]
        assert len(start_logs) == 1
        assert "gpt-4o-mini" in start_logs[0].message
        assert "gpt-4.1" not in start_logs[0].message


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, base_url=None, api_key=None):
        time.sleep(0.05)  # widen the init race window
        self.base_url = base_url
        self.api_key = api_key
        _FakeOpenAI.instances.append(self)


class TestSharedClientInitAndConfig:
    """Exactly-one lazy init and config-update safety under concurrency."""

    def test_concurrent_lazy_init_exactly_one_client(self, monkeypatch):
        _FakeOpenAI.instances = []
        monkeypatch.setattr("gigaam_transcriber.summarizer.OpenAI", _FakeOpenAI)
        client = LLMClient(LLMClientConfig(api_key="sk-test", model="gpt-4.1"))

        errors: list[Exception] = []

        def worker() -> None:
            try:
                assert client._get_client() is not None
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert len(_FakeOpenAI.instances) == 1, (
            f"lazy init raced: {len(_FakeOpenAI.instances)} clients created for one LLMClient"
        )

    def test_update_config_during_in_flight_call(self, monkeypatch):
        _FakeOpenAI.instances = []
        monkeypatch.setattr("gigaam_transcriber.summarizer.OpenAI", _FakeOpenAI)

        client = LLMClient(LLMClientConfig(api_key="sk-test", model="gpt-4.1"))
        mock_openai = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "ok"
        response.usage.prompt_tokens = 1
        response.usage.completion_tokens = 1

        def slow_create(**kwargs):
            time.sleep(0.3)
            return response

        mock_openai.chat.completions.create.side_effect = slow_create
        client._client = mock_openai

        call_result: list[str] = []
        call_error: list[Exception] = []

        def in_flight_call() -> None:
            try:
                call_result.append(client.call("sys", "user"))
            except Exception as exc:  # pragma: no cover - failure path
                call_error.append(exc)

        thread = threading.Thread(target=in_flight_call)
        thread.start()
        time.sleep(0.05)  # call is now inside the (slow) API request

        for i in range(5):
            client.update_config("http://new", f"sk-{i}", "gpt-4o-mini")

        thread.join()

        assert not call_error, call_error
        assert call_result == ["ok"], "in-flight call must survive config updates"
        assert client.config.base_url == "http://new"
        assert client.config.model == "gpt-4o-mini"
        assert client._client is None
        # Next call lazily rebuilds the client exactly once under the new config.
        client._get_client()
        client._get_client()
        assert len(_FakeOpenAI.instances) == 1
        assert _FakeOpenAI.instances[0].base_url == "http://new"
