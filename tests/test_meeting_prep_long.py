"""Tests for map-reduce chunking in meeting_prep for long texts.

Verifies:
- Short data → single call (backward compat)
- Long company_data → chunking + map-reduce
- Empty/edge cases handled gracefully
- model_override uses call() param, not update_config()
- Logging of chunking decisions
"""

import logging
import pytest
from unittest.mock import MagicMock, call, patch

from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(api_key="sk-test", model="gpt-4o-mini"):
    """Create LLMClient with real config but mocked OpenAI client."""
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


def _make_mock_llm_client(model="gpt-4o-mini"):
    """Create a mock LLMClient that tracks calls without hitting OpenAI."""
    mock = MagicMock(spec=LLMClient)
    mock.config = LLMClientConfig(api_key="sk-test", model=model)
    mock._client = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# 1. Short text → single call (backward compat)
# ---------------------------------------------------------------------------


class TestShortTextSingleCall:
    """Short company_data + catalog_data → single LLM call, no chunking."""

    @pytest.mark.asyncio
    async def test_short_data_single_call(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки\n## Профиль компании\nТест"
        )

        result, model_used = await generate_meeting_prep(
            "Company: TestCorp", "Product: Widget", client
        )

        assert "План подготовки" in result
        assert model_used == "gpt-4o-mini"
        # Only one LLM call (no map-reduce)
        assert mock_openai.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_short_data_with_model_override(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки"
        )

        result, model_used = await generate_meeting_prep(
            "Company: TestCorp",
            "Product: Widget",
            client,
            model_override="gpt-4.1",
        )

        assert model_used == "gpt-4.1"
        # Verify model_override is passed via call param, not update_config
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4.1"
        # Original config should NOT be mutated
        assert client.config.model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 2. Long company_data → chunking + map-reduce
# ---------------------------------------------------------------------------


class TestLongTextMapReduce:
    """Long company_data triggers map-reduce with multiple LLM calls."""

    @pytest.mark.asyncio
    async def test_long_data_triggers_map_reduce(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            # Map calls return chunk analysis, reduce call returns final
            if call_count[0] <= 3:
                return _mock_response(f"Анализ части {call_count[0]}: профиль, контакты")
            return _mock_response("# План подготовки к встрече\n## Профиль компании\nИтого")

        mock_openai.chat.completions.create.side_effect = side_effect

        # Create very long company_data to force chunking
        # Using gpt-4 (8192 ctx) to trigger chunking with moderate-sized text
        client._config = LLMClientConfig(api_key="sk-test", model="gpt-4")
        client._client = mock_openai

        long_company_data = ". ".join(
            [f"Компания ООО Ромашка. ИНН 1234567890. ОГРН 1234567890123. "
             f"Дата регистрации 01.01.2020. Юр. адрес: г. Москва, ул. Тестовая, д. {i}. "
             f"Основной ОКВЭД: 62.0{i} — Разработка ПО. "
             f"Сделка #{i} от 01.0{i+1}.2024 на сумму {i * 10000} руб. "
             for i in range(60)]
        )
        catalog_data = "Продукт 1: CRM-система. Продукт 2: ERP-модуль."

        result, model_used = await generate_meeting_prep(
            long_company_data, catalog_data, client
        )

        assert "План подготовки" in result
        assert model_used == "gpt-4"
        # Should have multiple calls: map chunks + reduce
        assert call_count[0] >= 2, f"Expected map-reduce (>=2 calls), got {call_count[0]}"

    @pytest.mark.asyncio
    async def test_long_data_with_model_override(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"Анализ части {call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        # Use gpt-4 with override to gpt-4
        client._config = LLMClientConfig(api_key="sk-test", model="gpt-4")
        client._client = mock_openai

        long_company_data = ". ".join(
            [f"Компания Тест №{i}. ИНН {i}. Описание: тестовая компания для проверки. "
             for i in range(60)]
        )

        result, model_used = await generate_meeting_prep(
            long_company_data, "Каталог продуктов", client,
            model_override="gpt-4.1",
        )

        assert model_used == "gpt-4.1"
        # All calls should use override model
        for c in mock_openai.chat.completions.create.call_args_list:
            assert c[1]["model"] == "gpt-4.1"


# ---------------------------------------------------------------------------
# 3. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty data, whitespace-only, etc."""

    @pytest.mark.asyncio
    async def test_empty_company_data_graceful(self):
        """Empty company_data with valid catalog_data → single call."""
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки\n## Справка\nДанные отсутствуют"
        )

        result, model_used = await generate_meeting_prep(
            "", "Каталог: продукт А", client
        )

        assert result  # Non-empty
        assert mock_openai.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_catalog_data(self):
        """Non-empty company_data with empty catalog_data → single call."""
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки\n## Справка\nКонтент"
        )

        result, model_used = await generate_meeting_prep(
            "Компания: Test", "", client
        )

        assert result
        assert mock_openai.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_whitespace_only_data(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки\nДанные отсутствуют"
        )

        result, _ = await generate_meeting_prep("   ", "   ", client)
        assert result

    @pytest.mark.asyncio
    async def test_llm_returns_empty_raises(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response("")

        with pytest.raises(ValueError, match="пустой результат"):
            await generate_meeting_prep("info", "catalog", client)


# ---------------------------------------------------------------------------
# 4. No update_config mutation
# ---------------------------------------------------------------------------


class TestNoUpdateConfigMutation:
    """Verify that model_override does NOT mutate client config."""

    @pytest.mark.asyncio
    async def test_model_override_no_mutation(self):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client(model="gpt-4o-mini")
        mock_openai.chat.completions.create.return_value = _mock_response(
            "# План подготовки к встрече"
        )

        original_model = client.config.model
        original_api_key = client.config.api_key
        original_base_url = client.config.base_url

        await generate_meeting_prep(
            "Short data", "Short catalog", client,
            model_override="gpt-4.1",
        )

        # Config must remain unchanged after call
        assert client.config.model == original_model
        assert client.config.api_key == original_api_key
        assert client.config.base_url == original_base_url

    @pytest.mark.asyncio
    async def test_no_update_config_called(self):
        """update_config() should NOT be called at all."""
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response("result")

        # Spy on update_config — it should NOT be called
        client.update_config = MagicMock()

        await generate_meeting_prep("info", "catalog", client, model_override="gpt-4.1")

        client.update_config.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Logging
# ---------------------------------------------------------------------------


class TestChunkingLogging:
    """Verify chunking decisions are logged."""

    @pytest.mark.asyncio
    async def test_single_call_logs_strategy(self, caplog):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        mock_openai.chat.completions.create.return_value = _mock_response("result")

        with caplog.at_level(logging.INFO, logger="gigaam_transcriber.meeting_prep.service"):
            await generate_meeting_prep("info", "catalog", client)

        meeting_prep_logs = [r for r in caplog.records
                             if r.name == "gigaam_transcriber.meeting_prep.service"]
        assert len(meeting_prep_logs) >= 1

    @pytest.mark.asyncio
    async def test_map_reduce_logs_chunking(self, caplog):
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return _mock_response(f"Анализ {call_count[0]}")

        mock_openai.chat.completions.create.side_effect = side_effect

        client._config = LLMClientConfig(api_key="sk-test", model="gpt-4")
        client._client = mock_openai

        long_data = ". ".join([f"Компания {i}. " * 10 for i in range(60)])

        with caplog.at_level(logging.INFO, logger="gigaam_transcriber.meeting_prep.service"):
            await generate_meeting_prep(long_data, "catalog", client)

        meeting_prep_logs = [r.message for r in caplog.records
                             if r.name == "gigaam_transcriber.meeting_prep.service"]
        assert any("chunk" in msg.lower() or "map-reduce" in msg.lower()
                    for msg in meeting_prep_logs)


# ---------------------------------------------------------------------------
# 6. Reduce stage must include catalog_data (CQ-H8)
# ---------------------------------------------------------------------------


def _mock_openai_response(text: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage.prompt_tokens = len(text)
    resp.usage.completion_tokens = len(text)
    return resp


class TestCatalogInReduce:
    @pytest.mark.asyncio
    async def test_map_reduce_reduce_prompt_contains_catalog(self):
        """Long-context reduce must actually send catalog_data to the LLM."""
        from gigaam_transcriber.meeting_prep.service import generate_meeting_prep

        client, mock_openai = _make_client()
        client._config = LLMClientConfig(api_key="sk-test", model="gpt-4")
        client._client = mock_openai
        call_count = [0]
        captured: list[list[dict]] = []

        def side_effect(**kwargs):
            call_count[0] += 1
            captured.append(kwargs["messages"])
            if call_count[0] <= 3:
                return _mock_openai_response(f"Анализ части {call_count[0]}")
            return _mock_openai_response("# План подготовки к встрече\nИтог")

        mock_openai.chat.completions.create.side_effect = side_effect

        long_company_data = ". ".join(
            f"Компания ООО Ромашка. ИНН 1234567890. Сделка #{i} на сумму {i * 1000} руб."
            for i in range(60)
        )
        catalog_data = "УНИКАЛЬНЫЙ_КАТАЛОГ_XYZ: CRM-система, ERP-модуль"

        result, model_used = await generate_meeting_prep(
            long_company_data, catalog_data, client
        )

        assert "План подготовки" in result
        assert model_used == "gpt-4"
        assert call_count[0] >= 2, "expected map-reduce (>= 2 calls)"

        map_users = [msgs[-1]["content"] for msgs in captured[:-1]]
        assert any("<company-data-part" in u for u in map_users)

        reduce_messages = captured[-1]
        reduce_user = reduce_messages[-1]["content"]
        assert "<catalog-data>" in reduce_user
        assert "УНИКАЛЬНЫЙ_КАТАЛОГ_XYZ" in reduce_user, (
            "catalog_data must reach the LLM in the reduce stage"
        )
        assert "каталог" in reduce_messages[0]["content"].lower()
