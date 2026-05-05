import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock

from gigaam_transcriber.template_manager import TemplateManager
from gigaam_transcriber.summarizer import SUMMARY_TEMPLATES, generate_summary
from gigaam_transcriber.autoflow import run_autoflow, AutoflowResult
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.database import engine, async_session_factory, Base


USER_ID = "test-user-e2e"


@pytest_asyncio.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def fake_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="Speaker 1: Hello. Speaker 2: Hi there. Speaker 1: How are you? Speaker 2: Fine thanks.",
        segments=[
            TranscriptionSegment(text="Hello.", start=0.0, end=1.0, speaker="1"),
            TranscriptionSegment(text="Hi there.", start=1.0, end=2.0, speaker="2"),
            TranscriptionSegment(text="How are you?", start=2.0, end=3.0, speaker="1"),
            TranscriptionSegment(text="Fine thanks.", start=3.0, end=4.0, speaker="2"),
        ],
        duration=4.0,
        language="en",
        model_name="test",
        processing_time=1.0,
    )
    return t


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.call.return_value = "# Summary\n## Key points\n- Point 1\n- Point 2"
    return llm


class TestE2E_CustomTemplateSummary:
    @pytest.mark.asyncio
    async def test_custom_template_in_merged(self, db_session):
        await TemplateManager.create_template(
            db_session, USER_ID, "🔬 Scientific", "You are a science analyst.",
        )
        merged = await TemplateManager.get_all_templates(db_session, USER_ID, SUMMARY_TEMPLATES)
        assert "custom-scientific" in merged
        assert merged["custom-scientific"]["label"] == "🔬 Scientific"

    @pytest.mark.asyncio
    async def test_generate_summary_uses_custom_template(self, db_session, fake_llm):
        await TemplateManager.create_template(
            db_session, USER_ID, "🔬 Scientific", "You are a science analyst.",
        )
        result = await generate_summary(
            "Some text", "custom-scientific", fake_llm,
            db=db_session, user_id=USER_ID,
        )
        assert result == "# Summary\n## Key points\n- Point 1\n- Point 2"
        fake_llm.call.assert_called_once()
        system_prompt = fake_llm.call.call_args[0][0]
        assert "science analyst" in system_prompt


class TestE2E_AutoflowFull:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, fake_transcriber, fake_llm):
        config = {"diarization": "none"}
        result = await run_autoflow(
            "test.mp3", "general", fake_llm, config,
            transcriber=fake_transcriber,
        )

        assert isinstance(result, AutoflowResult)
        assert result.transcription_result is not None
        assert result.transcription_result.text != ""
        assert result.summary_text != ""
        assert result.mindmap_html != ""
        assert result.mindmap_md != ""
        assert result.errors == []
        assert "transcription" in result.stage_timings
        assert "summary" in result.stage_timings
        assert "mindmap" in result.stage_timings

    @pytest.mark.asyncio
    async def test_result_state_populated(self, fake_transcriber, fake_llm):
        config = {"diarization": "none"}
        result = await run_autoflow(
            "test.mp3", "meeting", fake_llm, config,
            transcriber=fake_transcriber,
        )
        tr = result.transcription_result
        assert len(tr.segments) == 4
        assert tr.duration == 4.0

    @pytest.mark.asyncio
    async def test_autoflow_with_custom_template(self, db_session, fake_transcriber, fake_llm):
        await TemplateManager.create_template(
            db_session, USER_ID, "🔬 Scientific", "You are a science analyst.",
        )
        config = {"diarization": "none"}
        result = await run_autoflow(
            "test.mp3", "custom-scientific", fake_llm, config,
            transcriber=fake_transcriber,
            db=db_session, user_id=USER_ID,
        )
        assert isinstance(result, AutoflowResult)
        assert result.summary_text != ""
        assert result.errors == []


class TestE2E_AutoflowPartial:
    @pytest.mark.asyncio
    async def test_summary_error_partial_result(self, fake_transcriber):
        llm = MagicMock()
        llm.call.side_effect = ConnectionError("API unreachable")

        config = {"diarization": "none"}
        result = await run_autoflow(
            "test.mp3", "general", llm, config,
            transcriber=fake_transcriber,
        )

        assert result.transcription_result is not None
        assert result.summary_text == ""
        assert result.mindmap_html == ""
        assert len(result.errors) > 0
        assert any("Саммари" in e for e in result.errors)


class TestE2E_BuiltinTemplatesUnchanged:
    def test_builtin_keys_exist(self):
        assert "meeting" in SUMMARY_TEMPLATES
        assert "lecture" in SUMMARY_TEMPLATES
        assert "interview" in SUMMARY_TEMPLATES
        assert "general" in SUMMARY_TEMPLATES

    def test_builtin_template_structure(self):
        for key, t in SUMMARY_TEMPLATES.items():
            assert "label" in t
            assert "system_prompt" in t
            assert isinstance(t["label"], str)
            assert isinstance(t["system_prompt"], str)
            assert len(t["label"]) > 0
            assert len(t["system_prompt"]) > 0

    @pytest.mark.asyncio
    async def test_merged_contains_builtin(self, db_session):
        merged = await TemplateManager.get_all_templates(db_session, USER_ID, SUMMARY_TEMPLATES)
        for key in SUMMARY_TEMPLATES:
            assert key in merged
            assert merged[key]["label"] == SUMMARY_TEMPLATES[key]["label"]
            assert merged[key]["system_prompt"] == SUMMARY_TEMPLATES[key]["system_prompt"]


class TestE2E_StepByStepStillWorks:
    def test_step_by_step_transcription(self, fake_transcriber):
        result = fake_transcriber.transcribe("test.mp3", diarization="none")
        assert result.text != ""
        assert len(result.segments) > 0

    @pytest.mark.asyncio
    async def test_step_by_step_summary(self, fake_llm):
        summary = await generate_summary("Some transcription text.", "meeting", fake_llm)
        assert summary == "# Summary\n## Key points\n- Point 1\n- Point 2"

    def test_step_by_step_mindmap(self, fake_llm):
        from gigaam_transcriber.mindmap import generate_mindmap_markdown
        md = generate_mindmap_markdown("Some transcription text.", fake_llm)
        assert md != ""
        assert "#" in md
