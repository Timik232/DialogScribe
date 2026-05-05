"""Unit-тесты для summarizer.py — LLMClient, chunking, map-reduce, шаблоны."""

import json
import pytest
from unittest.mock import patch, MagicMock

from gigaam_transcriber.summarizer import (
    LLMClient,
    LLMClientConfig,
    SUMMARY_TEMPLATES,
    generate_summary,
    get_available_models,
    parse_models_csv,
    split_text,
    summary_to_html,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)
from gigaam_transcriber.context_utils import estimate_tokens


# ===== LLMClient tests (task 9.1) =====


class TestLLMClientConfig:
    def test_defaults(self):
        config = LLMClientConfig()
        assert config.base_url == DEFAULT_BASE_URL
        assert config.api_key == ""
        assert config.model == DEFAULT_MODEL

    def test_custom(self):
        config = LLMClientConfig(
            base_url="https://custom.api.com/v1",
            api_key="sk-test",
            model="gpt-4",
        )
        assert config.base_url == "https://custom.api.com/v1"
        assert config.api_key == "sk-test"
        assert config.model == "gpt-4"


class TestModelSelection:
    def test_parse_models_csv_empty(self):
        assert parse_models_csv(None) == []
        assert parse_models_csv("") == []

    def test_parse_models_csv_trim_and_filter(self):
        assert parse_models_csv(" gpt-4.1 , ,gpt-4o-mini , gpt-4.1 ") == [
            "gpt-4.1",
            "gpt-4o-mini",
            "gpt-4.1",
        ]

    def test_get_available_models_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_MODELS", "gpt-4.1,gpt-4o-mini,gpt-4.1")
        monkeypatch.setenv("LLM_MODEL", "ignored-default")
        assert get_available_models() == ["gpt-4.1", "gpt-4o-mini"]

    def test_get_available_models_fallback_to_default_model(self, monkeypatch):
        monkeypatch.delenv("LLM_MODELS", raising=False)
        monkeypatch.setenv("LLM_MODEL", "gpt-4.1")
        assert get_available_models() == ["gpt-4.1"]


class TestLLMClient:
    def _make_client(self, api_key="sk-test"):
        config = LLMClientConfig(api_key=api_key)
        return LLMClient(config)

    def test_init_default(self):
        client = LLMClient()
        assert client.config.base_url == DEFAULT_BASE_URL
        assert isinstance(client.config.api_key, str)

    def test_call_no_api_key_raises(self):
        client = LLMClient(LLMClientConfig(api_key=""))
        with pytest.raises(ValueError, match="API key"):
            client.call("system", "user")

    def test_call_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test summary"

        with patch.object(client, "_get_client") as mock_get:
            mock_openai = MagicMock()
            mock_openai.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_openai

            # Need to bypass lazy init
            client._client = mock_openai
            result = client.call("system prompt", "user text")
            assert result == "Test summary"
            mock_openai.chat.completions.create.assert_called_once()

    def test_call_auth_error(self):
        from openai import AuthenticationError

        client = self._make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad API key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with pytest.raises(ValueError, match="авторизации"):
            client.call("system", "user")

    def test_call_connection_error(self):
        from openai import APIConnectionError

        client = self._make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
        client._client = mock_openai

        with pytest.raises(ConnectionError):
            client.call("system", "user")

    def test_call_rate_limit(self):
        from openai import RateLimitError

        client = self._make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = RateLimitError(
            message="Rate limited", response=MagicMock(), body=None
        )
        client._client = mock_openai

        with pytest.raises(RuntimeError, match="лимит"):
            client.call("system", "user")

    def test_update_config(self):
        client = self._make_client()
        client._client = MagicMock()  # simulate existing client
        client.update_config("https://new.api.com/v1", "sk-new", "gpt-4")
        assert client.config.base_url == "https://new.api.com/v1"
        assert client.config.api_key == "sk-new"
        assert client.config.model == "gpt-4"
        assert client._client is None  # forced reinit

    def test_test_connection_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_response
        client._client = mock_openai

        success, msg = client.test_connection()
        assert success is True
        assert "успешно" in msg

    def test_test_connection_no_key(self):
        client = LLMClient(LLMClientConfig(api_key=""))
        success, msg = client.test_connection()
        assert success is False
        assert "API key" in msg

    def test_test_connection_auth_fail(self):
        from openai import AuthenticationError

        client = self._make_client()
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = AuthenticationError(
            message="Bad key", response=MagicMock(), body=None
        )
        client._client = mock_openai

        success, msg = client.test_connection()
        assert success is False
        assert "Неверный" in msg


# ===== Chunking tests (task 9.2) =====


class TestSplitText:
    def test_short_text_single_chunk(self):
        text = "Короткий текст."
        chunks = split_text(text, max_tokens=10000)
        assert chunks == [text]

    def test_long_text_splits(self):
        # Create text that's clearly over max_tokens
        text = ". ".join([f"Предложение номер {i} для тестирования" for i in range(500)])
        chunks = split_text(text, max_tokens=100, overlap_sentences=1)
        assert len(chunks) > 1

    def test_overlap_between_chunks(self):
        text = ". ".join([f"Предложение {i}" for i in range(100)])
        chunks = split_text(text, max_tokens=100, overlap_sentences=2)
        # Check overlap: last sentences of chunk[i] should be first sentences of chunk[i+1]
        if len(chunks) > 1:
            last_sentences = chunks[0].split(". ")[-2:]
            next_start = ". ".join(last_sentences)
            assert next_start in chunks[1] or chunks[1].startswith(last_sentences[0])

    def test_single_long_paragraph(self):
        """Text without sentence boundaries."""
        text = "а " * 5000
        chunks = split_text(text, max_tokens=100)
        assert len(chunks) > 1

    def test_empty_text(self):
        chunks = split_text("", max_tokens=100)
        assert chunks == [""]


class TestEstimateTokens:
    def test_estimate(self):
        text = "Привет мир"
        tokens = estimate_tokens(text)
        assert tokens > 0


# ===== Map-reduce tests (task 9.2) =====


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_single_chunk(self):
        client = LLMClient(LLMClientConfig(api_key="sk-test"))
        mock_openai = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "## Саммари\n- пункт 1"
        mock_openai.chat.completions.create.return_value = mock_response
        client._client = mock_openai

        result = await generate_summary("Короткий текст.", "general", client)
        assert "## Саммари" in result
        assert mock_openai.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_map_reduce(self):
        client = LLMClient(LLMClientConfig(api_key="sk-test"))
        mock_openai = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # Return different content for each call
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = f"Chunk summary {call_count[0]}"
            return resp

        mock_openai.chat.completions.create.side_effect = side_effect
        client._client = mock_openai

        long_text = ". ".join([f"Предложение номер {i} для тестирования разбиения длинного текста на части" for i in range(500)])
        result = await generate_summary(
            long_text,
            "general",
            client,
        )
        # Should have multiple calls: chunk summaries + reduce
        assert call_count[0] >= 2

    @pytest.mark.asyncio
    async def test_invalid_template(self):
        client = LLMClient(LLMClientConfig(api_key="sk-test"))
        with pytest.raises(ValueError, match="Неизвестный шаблон"):
            await generate_summary("text", "nonexistent", client)


# ===== Summary to HTML tests (task 9.4) =====


class TestSummaryToHtml:
    def test_markdown_to_html(self):
        md = "## Заголовок\n\n- пункт 1\n- пункт 2"
        html = summary_to_html(md)
        assert "<h2>" in html
        assert "<li>" in html

    def test_table_rendering(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = summary_to_html(md)
        assert "<table>" in html


# ===== Templates tests (task 9.4) =====


class TestSummaryTemplates:
    def test_all_templates_exist(self):
        assert "meeting" in SUMMARY_TEMPLATES
        assert "lecture" in SUMMARY_TEMPLATES
        assert "interview" in SUMMARY_TEMPLATES
        assert "general" in SUMMARY_TEMPLATES

    def test_template_has_label(self):
        for key, tpl in SUMMARY_TEMPLATES.items():
            assert "label" in tpl, f"Template {key} missing 'label'"
            assert tpl["label"]

    def test_template_has_system_prompt(self):
        for key, tpl in SUMMARY_TEMPLATES.items():
            assert "system_prompt" in tpl, f"Template {key} missing 'system_prompt'"
            assert len(tpl["system_prompt"]) > 50

    def test_templates_contain_markdown_headers(self):
        for key, tpl in SUMMARY_TEMPLATES.items():
            assert "##" in tpl["system_prompt"], f"Template {key} should use Markdown headers"
