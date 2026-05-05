from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.autoflow import AutoflowResult, run_autoflow
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment


@pytest.fixture
def fake_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="Hello world. This is a test transcription.",
        segments=[TranscriptionSegment(text="Hello world.", start=0.0, end=2.0)],
        duration=2.0,
        language="en",
        model_name="test",
        processing_time=1.0,
    )
    return t


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.call.return_value = "# Summary\n- Point 1"
    return llm


@pytest.fixture
def config():
    return {"diarization": "none"}


class TestRunAutoflow:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, fake_transcriber, fake_llm, config):
        progress = MagicMock()
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber, progress_callback=progress,
        )

        assert result.transcription_result is not None
        assert result.summary_text != ""
        assert result.mindmap_md != ""
        assert result.mindmap_html != ""
        assert result.errors == []
        assert "transcription" in result.stage_timings
        assert "summary" in result.stage_timings
        assert "mindmap" in result.stage_timings

    @pytest.mark.asyncio
    async def test_transcription_failure(self, fake_transcriber, fake_llm, config):
        fake_transcriber.transcribe.side_effect = Exception("API down")
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber,
        )
        assert result.transcription_result is None
        assert any("Транскрибация" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_summary_failure_partial(self, fake_transcriber, fake_llm, config):
        fake_llm.call.side_effect = Exception("LLM error")
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber,
        )
        assert result.transcription_result is not None
        assert any("Саммари" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, fake_transcriber, fake_llm, config):
        progress = MagicMock()
        await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber, progress_callback=progress,
        )
        calls = progress.call_args_list
        assert len(calls) >= 3

    @pytest.mark.asyncio
    async def test_unknown_template(self, fake_transcriber, fake_llm, config):
        result = await run_autoflow(
            "test.mp3", "nonexistent", fake_llm, config,
            transcriber=fake_transcriber,
        )
        assert result.transcription_result is not None
        assert any("не найден" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_empty_transcription(self, fake_transcriber, fake_llm, config):
        fake_transcriber.transcribe.return_value = TranscriptionResult(
            text="", segments=[], duration=0.0, language="en",
            model_name="test", processing_time=0.0,
        )
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber,
        )
        assert any("пуста" in e.lower() for e in result.errors)


class TestAutoflowWithInsights:
    @pytest.mark.asyncio
    async def test_with_insights_enabled(self, fake_transcriber, fake_llm, config):
        with (
            patch("gigaam_transcriber.autoflow.extract_action_items", return_value={"action_items": [], "decisions": []}),
            patch("gigaam_transcriber.autoflow.generate_suggested_steps", return_value={"suggested_steps": []}),
        ):
            result = await run_autoflow(
                "test.mp3", "general", fake_llm, config,
                transcriber=fake_transcriber,
                include_insights=True,
            )

        assert result.action_items is not None
        assert result.suggested_steps is not None
        assert "insights" in result.stage_timings

    @pytest.mark.asyncio
    async def test_without_insights_default(self, fake_transcriber, fake_llm, config):
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber,
        )
        assert result.action_items is None
        assert result.suggested_steps is None

    @pytest.mark.asyncio
    async def test_insights_failure_does_not_break_pipeline(self, fake_transcriber, fake_llm, config):
        with patch("gigaam_transcriber.autoflow.extract_action_items", side_effect=Exception("LLM error")):
            result = await run_autoflow(
                "test.mp3", "general", fake_llm, config,
                transcriber=fake_transcriber,
                include_insights=True,
            )

        assert result.transcription_result is not None
        assert any("Инсайты" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_insights_progress_callback(self, fake_transcriber, fake_llm, config):
        progress = MagicMock()
        with (
            patch("gigaam_transcriber.autoflow.extract_action_items", return_value={"action_items": [], "decisions": []}),
            patch("gigaam_transcriber.autoflow.generate_suggested_steps", return_value={"suggested_steps": []}),
        ):
            await run_autoflow(
                "test.mp3", "general", fake_llm, config,
                transcriber=fake_transcriber,
                progress_callback=progress,
                include_insights=True,
            )

        messages = [c[0][0] for c in progress.call_args_list]
        assert any("инсайт" in m.lower() for m in messages)
