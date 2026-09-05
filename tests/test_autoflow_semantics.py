"""Autoflow end-to-end semantics tests (Task 9).

Covers: stage-enum flag gating (skipped events + absent outputs), model
propagation to every enabled LLM stage, quota ordering (preflight oversize
consumes nothing, failed ASR consumes no usage, success tracks exactly one
event of each type with the right minutes), unique per-connection session
IDs, mid-stage cancellation, and monotonic seq with terminal-last ordering
including skipped-stage events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

import gigaam_transcriber.ws_protocol as ws_protocol
import routers.autoflow as autoflow_router
from gigaam_transcriber.auth import create_access_token
from gigaam_transcriber.autoflow import AutoflowStage, StageEvent, run_autoflow
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.database import Base
from gigaam_transcriber.models import UsageEvent

USER_ID = "22222222-2222-2222-2222-222222222222"


def make_transcription(duration: float = 120.0) -> TranscriptionResult:
    return TranscriptionResult(
        text="Hello world. This is a test transcription.",
        segments=[TranscriptionSegment(text="Hello world.", start=0.0, end=2.0)],
        duration=duration,
        language="en",
        model_name="test",
        processing_time=1.0,
    )


def make_user_row():
    return SimpleNamespace(id=USER_ID, email="u@example.com", role="user", is_active=True)


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("WS_MAX_FRAME_BYTES", str(64 * 1024))
    monkeypatch.setenv("WS_MAX_TOTAL_BYTES", str(256 * 1024))
    return str(tmp_path)


def auth_frame(token: str | None = None) -> str:
    return json.dumps(
        {"type": "auth", "token": token or create_access_token(USER_ID, "user"), "protocol": 1}
    )


def expect_close(ws, code: int | None = None) -> int:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            ws.receive_text()
    if code is not None:
        assert excinfo.value.code == code
    return excinfo.value.code


def ds_files(tmp_dir: str) -> list[str]:
    return [p for p in os.listdir(tmp_dir) if p.startswith("ds_ws_")]


def _patched_upload_init(tmp_dir: str):
    original = ws_protocol.BinaryUpload.__init__

    def init(self, ws, **kwargs):
        kwargs.setdefault("tmp_dir", tmp_dir)
        original(self, ws, **kwargs)

    return init


class FakeLLMClient:
    """Returns real strings — MagicMock payloads crash the JSON sender."""

    def __init__(self, config=None):
        self.config = SimpleNamespace(model="fake-model")

    def call(self, prompt: str, text: str, **kwargs) -> str:
        return "# Итог\n## Тезис 1\n- деталь"


class RouterHarness:
    """Real autoflow WS router with mocked auth/quota seams and spy hooks."""

    def __init__(self, monkeypatch, tmp_dir: str, *, run_impl=None):
        self.captured: dict = {}
        self.check_limit = AsyncMock()
        self.track_usage = AsyncMock()

        async def fake_run(**kwargs):
            self.captured.update(kwargs)
            if run_impl is not None:
                return await run_impl(**kwargs)
            return SimpleNamespace(
                errors=[],
                stage_timings={"transcription": 1.0},
                transcription_result=make_transcription(),
                summary_text="# Summary",
                mindmap_md="# Map",
                action_items=None,
                suggested_steps=None,
            )

        monkeypatch.setattr(autoflow_router, "run_autoflow", fake_run)
        monkeypatch.setattr(autoflow_router, "LLMClient", FakeLLMClient)
        monkeypatch.setattr(autoflow_router, "check_limit", self.check_limit)
        monkeypatch.setattr(autoflow_router, "track_usage", self.track_usage)

        auth_session = MagicMock()
        auth_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
        )
        work_session = MagicMock()
        work_session.execute = AsyncMock(
            return_value=MagicMock(scalar=lambda: 0.0, scalar_one_or_none=lambda: None)
        )
        work_session.commit = AsyncMock()

        @contextlib.asynccontextmanager
        async def auth_factory():
            yield auth_session

        @contextlib.asynccontextmanager
        async def work_factory():
            yield work_session

        monkeypatch.setattr(ws_protocol, "async_session_factory", auth_factory)
        monkeypatch.setattr(autoflow_router, "async_session_factory", work_factory)
        monkeypatch.setattr(ws_protocol.BinaryUpload, "__init__", _patched_upload_init(tmp_dir))

        self.app = FastAPI()
        self.app.include_router(autoflow_router.router)
        self.app.state.transcriber = MagicMock(name="transcriber")

    def run(
        self,
        payload: bytes,
        *,
        filename: str = "a.wav",
        extra_meta: dict | None = None,
        after_upload=None,
    ) -> list[dict]:
        """Drive one WS run; returns every server message (terminal included)."""
        messages: list[dict] = []
        with TestClient(self.app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                messages.append(ws.receive_json())
                meta = {"type": "meta", "bytes": len(payload), "filename": filename}
                meta.update(extra_meta or {})
                ws.send_text(json.dumps(meta))
                messages.append(ws.receive_json())
                chunk = 1024
                for off in range(0, len(payload), chunk):
                    ws.send_bytes(payload[off : off + chunk])
                if after_upload is not None:
                    after_upload(ws)
                while True:
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg.get("type") in ("complete", "error"):
                        break
        return messages


# ---------------------------------------------------------------------------
# run_autoflow level: flag gating + model propagation
# ---------------------------------------------------------------------------


class TestFlagGating:
    @pytest.fixture
    def fake_transcriber(self):
        t = MagicMock()
        t.transcribe.return_value = make_transcription()
        return t

    @pytest.fixture
    def llm(self):
        return FakeLLMClient()

    def _events(self, progress):
        return [c.args[0] for c in progress.call_args_list]

    @pytest.mark.asyncio
    async def test_summary_off_emits_single_skip_and_no_output(self, fake_transcriber, llm):
        progress = MagicMock()
        with patch("gigaam_transcriber.autoflow.generate_summary", new_callable=AsyncMock) as gs:
            result = await run_autoflow(
                "test.wav", "general", llm, {"diarization": "none"},
                transcriber=fake_transcriber, progress_callback=progress,
                include_summary=False,
            )
        gs.assert_not_called()
        assert result.summary_text == ""
        skips = [
            e for e in self._events(progress)
            if e.stage is AutoflowStage.SKIPPED and e.skipped_stage is AutoflowStage.SUMMARY
        ]
        assert len(skips) == 1
        assert all(e.stage is not AutoflowStage.SUMMARY for e in self._events(progress))

    @pytest.mark.asyncio
    async def test_mindmap_off_emits_single_skip_and_no_output(self, fake_transcriber, llm):
        progress = MagicMock()
        with patch("gigaam_transcriber.autoflow.generate_mindmap_markdown") as gm:
            result = await run_autoflow(
                "test.wav", "general", llm, {"diarization": "none"},
                transcriber=fake_transcriber, progress_callback=progress,
                include_mindmap=False,
            )
        gm.assert_not_called()
        assert result.mindmap_md == ""
        skips = [
            e for e in self._events(progress)
            if e.stage is AutoflowStage.SKIPPED and e.skipped_stage is AutoflowStage.MINDMAP
        ]
        assert len(skips) == 1

    @pytest.mark.asyncio
    async def test_both_off_still_transcribes(self, fake_transcriber, llm):
        result = await run_autoflow(
            "test.wav", "general", llm, {"diarization": "none"},
            transcriber=fake_transcriber,
            include_summary=False, include_mindmap=False,
        )
        assert result.transcription_result is not None
        assert result.summary_text == "" and result.mindmap_md == ""
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_mindmap_independent_of_summary_failure(self, fake_transcriber, llm):
        with (
            patch("gigaam_transcriber.autoflow.generate_summary", new_callable=AsyncMock) as gs,
            patch("gigaam_transcriber.autoflow.generate_mindmap_markdown") as gm,
        ):
            gs.side_effect = RuntimeError("llm down")
            gm.return_value = "# Map\n## Branch"
            result = await run_autoflow(
                "test.wav", "general", llm, {"diarization": "none"},
                transcriber=fake_transcriber,
            )
        assert result.summary_text == ""
        assert result.mindmap_md == "# Map\n## Branch"
        gm.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_template_only_fails_summary(self, fake_transcriber, llm):
        with patch("gigaam_transcriber.autoflow.generate_mindmap_markdown") as gm:
            gm.return_value = "# Map"
            result = await run_autoflow(
                "test.wav", "nonexistent", llm, {"diarization": "none"},
                transcriber=fake_transcriber,
            )
        assert any("не найден" in e for e in result.errors)
        assert result.mindmap_md == "# Map"

    @pytest.mark.asyncio
    async def test_disabled_summary_skips_template_lookup(self, fake_transcriber, llm):
        result = await run_autoflow(
            "test.wav", "nonexistent", llm, {"diarization": "none"},
            transcriber=fake_transcriber, include_summary=False,
        )
        assert result.errors == []


class TestModelPropagation:
    @pytest.fixture
    def fake_transcriber(self):
        t = MagicMock()
        t.transcribe.return_value = make_transcription()
        return t

    @pytest.mark.asyncio
    async def test_model_reaches_every_llm_stage(self, fake_transcriber):
        llm = FakeLLMClient()
        with (
            patch("gigaam_transcriber.autoflow.generate_summary", new_callable=AsyncMock) as gs,
            patch("gigaam_transcriber.autoflow.generate_mindmap_markdown") as gm,
            patch("gigaam_transcriber.autoflow.extract_action_items") as ea,
            patch("gigaam_transcriber.autoflow.generate_suggested_steps") as gss,
        ):
            await run_autoflow(
                "test.wav", "general", llm, {"diarization": "none"},
                transcriber=fake_transcriber, include_insights=True,
                model="custom-model-x",
            )
        assert gs.call_args.kwargs.get("model") == "custom-model-x"
        assert gm.call_args.kwargs.get("model") == "custom-model-x"
        assert ea.call_args.kwargs.get("model") == "custom-model-x"
        assert gss.call_args.kwargs.get("model") == "custom-model-x"

    @pytest.mark.asyncio
    async def test_default_model_is_none(self, fake_transcriber):
        with patch("gigaam_transcriber.autoflow.generate_summary", new_callable=AsyncMock) as gs:
            await run_autoflow(
                "test.wav", "general", FakeLLMClient(), {"diarization": "none"},
                transcriber=fake_transcriber,
            )
        assert gs.call_args.kwargs.get("model") is None


# ---------------------------------------------------------------------------
# Router level: meta propagation, session ids, quota, cancellation, ordering
# ---------------------------------------------------------------------------


class TestRouterMetaPropagation:
    def test_meta_flags_and_model_reach_run_autoflow(self, ws_env, monkeypatch):
        harness = RouterHarness(monkeypatch, ws_env)
        payload = os.urandom(512)
        messages = harness.run(
            payload,
            extra_meta={
                "include_summary": False,
                "include_mindmap": False,
                "include_insights": True,
                "model": "gpt-4o-mini",
            },
        )
        assert messages[-1]["type"] == "complete"
        assert harness.captured["include_summary"] is False
        assert harness.captured["include_mindmap"] is False
        assert harness.captured["include_insights"] is True
        assert harness.captured["model"] == "gpt-4o-mini"

    def test_skipped_stage_events_json_shape(self, ws_env, monkeypatch):
        async def run_impl(**kwargs):
            cb = kwargs["progress_callback"]
            cb(StageEvent(AutoflowStage.TRANSCRIBE, 0.35, "Транскрибация завершена"))
            cb(StageEvent(AutoflowStage.SKIPPED, 0.4, "Саммари отключено",
                          skipped_stage=AutoflowStage.SUMMARY))
            cb(StageEvent(AutoflowStage.SKIPPED, 0.8, "Майндмэп отключён",
                          skipped_stage=AutoflowStage.MINDMAP))
            return SimpleNamespace(
                errors=[], stage_timings={}, transcription_result=make_transcription(),
                summary_text="", mindmap_md="", action_items=None, suggested_steps=None,
            )

        harness = RouterHarness(monkeypatch, ws_env, run_impl=run_impl)
        messages = harness.run(os.urandom(64))
        skipped = [m for m in messages if m.get("stage") == AutoflowStage.SKIPPED.value]
        assert {s["skipped_stage"] for s in skipped} == {"summary", "mindmap"}
        assert all(s["type"] == "progress" for s in skipped)


class TestSessionIds:
    def test_unique_session_ids_across_runs(self, ws_env, monkeypatch):
        harness = RouterHarness(monkeypatch, ws_env)
        first = harness.run(os.urandom(64))
        second = harness.run(os.urandom(64))
        ids = set()
        for messages in (first, second):
            auth_ok = messages[0]
            complete = messages[-1]
            assert auth_ok["type"] == "auth_ok" and auth_ok["session_id"]
            assert complete["type"] == "complete"
            assert complete["session_id"] == auth_ok["session_id"]
            ids.add(auth_ok["session_id"])
        assert len(ids) == 2


class TestQuotaOrdering:
    def test_oversize_rejected_before_quota_check(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_MAX_TOTAL_BYTES", "1024")
        harness = RouterHarness(monkeypatch, ws_env)
        with TestClient(harness.app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "meta", "bytes": 4096, "filename": "a.wav"}))
                expect_close(ws, 4413)
        assert harness.check_limit.await_count == 0
        assert harness.track_usage.await_count == 0
        assert harness.captured == {}
        assert ds_files(ws_env) == []

    def test_limit_exceeded_terminal_error_no_run_no_track(self, ws_env, monkeypatch):
        from fastapi import HTTPException

        harness = RouterHarness(monkeypatch, ws_env)
        harness.check_limit.side_effect = HTTPException(
            status_code=429, detail="Usage limit exceeded for transcription_minutes"
        )
        messages = harness.run(os.urandom(64))
        assert messages[-1]["type"] == "error"
        assert messages[-1]["code"] == "limit_exceeded"
        assert "лимит" in messages[-1]["message"].lower()
        assert harness.check_limit.await_count == 1
        assert harness.track_usage.await_count == 0
        assert harness.captured == {}

    def test_failed_asr_checks_but_does_not_track(self, ws_env, monkeypatch):
        async def run_impl(**kwargs):
            kwargs["progress_callback"](
                StageEvent(AutoflowStage.TRANSCRIBE, 1.0, "Ошибка транскрибации")
            )
            return SimpleNamespace(
                errors=["Транскрибация: boom"], stage_timings={},
                transcription_result=None, summary_text="", mindmap_md="",
                action_items=None, suggested_steps=None,
            )

        harness = RouterHarness(monkeypatch, ws_env, run_impl=run_impl)
        messages = harness.run(os.urandom(64))
        assert messages[-1]["type"] == "complete"
        assert harness.check_limit.await_count == 1
        assert harness.track_usage.await_count == 0

    def test_success_tracks_exactly_one_event_per_type(self, ws_env, monkeypatch):
        harness = RouterHarness(monkeypatch, ws_env)
        messages = harness.run(os.urandom(64))
        assert messages[-1]["type"] == "complete"
        minutes_calls = [
            c for c in harness.track_usage.await_args_list
            if c.args[2] == "transcription_minutes"
        ]
        upload_calls = [
            c for c in harness.track_usage.await_args_list
            if c.args[2] == "file_upload"
        ]
        assert len(minutes_calls) == 1 and minutes_calls[0].args[3] == 2.0  # 120s → 2 min
        assert len(upload_calls) == 1 and upload_calls[0].args[3] == 1.0


class TestCancellation:
    def test_cancel_frame_stops_work_and_skips_usage(self, ws_env, monkeypatch):
        state = {"cancelled": False, "finished": False}

        async def run_impl(**kwargs):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            state["finished"] = True
            return SimpleNamespace(
                errors=[], stage_timings={}, transcription_result=make_transcription(),
                summary_text="", mindmap_md="", action_items=None, suggested_steps=None,
            )

        harness = RouterHarness(monkeypatch, ws_env, run_impl=run_impl)
        payload = os.urandom(256)
        with TestClient(harness.app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "meta", "bytes": len(payload), "filename": "a.wav"}))
                ws.receive_json()
                ws.send_bytes(payload)
                status = ws.receive_json()
                assert status["stage"] == AutoflowStage.UPLOAD.value
                ws.send_text(json.dumps({"type": "cancel"}))
                expect_close(ws)
        import time

        for _ in range(100):
            if state["cancelled"]:
                break
            time.sleep(0.05)
        assert state["cancelled"] is True
        assert state["finished"] is False
        assert harness.track_usage.await_count == 0
        assert ds_files(ws_env) == []

    def test_disconnect_mid_processing_skips_usage(self, ws_env, monkeypatch):
        release = asyncio.Event()
        state = {"cancelled": False}

        async def run_impl(**kwargs):
            try:
                await release.wait()
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            return SimpleNamespace(
                errors=[], stage_timings={}, transcription_result=make_transcription(),
                summary_text="", mindmap_md="", action_items=None, suggested_steps=None,
            )

        harness = RouterHarness(monkeypatch, ws_env, run_impl=run_impl)
        payload = os.urandom(256)
        with TestClient(harness.app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "meta", "bytes": len(payload), "filename": "a.wav"}))
                ws.receive_json()
                ws.send_bytes(payload)
                ws.receive_json()
        release.set()
        import time

        for _ in range(100):
            if state["cancelled"]:
                break
            time.sleep(0.05)
        assert state["cancelled"] is True
        assert harness.track_usage.await_count == 0
        assert ds_files(ws_env) == []


class TestOrdering:
    def test_monotonic_seq_including_skipped_terminal_last(self, ws_env, monkeypatch):
        async def run_impl(**kwargs):
            cb = kwargs["progress_callback"]
            for stage, progress in (
                (AutoflowStage.TRANSCRIBE, 0.05),
                (AutoflowStage.TRANSCRIBE, 0.35),
                (AutoflowStage.SUMMARY, 0.4),
                (AutoflowStage.SUMMARY, 0.6),
                (AutoflowStage.INSIGHTS, 0.65),
                (AutoflowStage.INSIGHTS, 0.75),
                (AutoflowStage.MINDMAP, 0.8),
                (AutoflowStage.MINDMAP, 0.95),
            ):
                cb(StageEvent(stage, progress, f"{stage.value} {progress}"))
            cb(StageEvent(AutoflowStage.SKIPPED, 0.9, "пропуск", skipped_stage=AutoflowStage.INSIGHTS))
            return SimpleNamespace(
                errors=[], stage_timings={}, transcription_result=make_transcription(),
                summary_text="s", mindmap_md="m", action_items=None, suggested_steps=None,
            )

        harness = RouterHarness(monkeypatch, ws_env, run_impl=run_impl)
        messages = harness.run(os.urandom(64))
        seqs = [m["seq"] for m in messages]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        assert messages[-1]["type"] == "complete"
        for m in messages[:-1]:
            assert m["type"] != "complete"


# ---------------------------------------------------------------------------
# Full stack: real run_autoflow through the real router + real in-memory DB
# ---------------------------------------------------------------------------


class TestFullStackIntegration:
    @pytest.mark.asyncio
    async def test_selective_run_persists_quota_and_omits_outputs(self, ws_env, monkeypatch):
        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


        @contextlib.asynccontextmanager
        async def real_work_factory():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        monkeypatch.setattr(autoflow_router, "async_session_factory", real_work_factory)
        monkeypatch.setattr(autoflow_router, "run_autoflow", run_autoflow)
        monkeypatch.setattr(autoflow_router, "LLMClient", FakeLLMClient)

        auth_session = MagicMock()
        auth_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
        )

        @contextlib.asynccontextmanager
        async def auth_factory():
            yield auth_session

        monkeypatch.setattr(ws_protocol, "async_session_factory", auth_factory)
        monkeypatch.setattr(ws_protocol.BinaryUpload, "__init__", _patched_upload_init(ws_env))

        transcriber = MagicMock()
        transcriber.transcribe.return_value = make_transcription(duration=60.0)

        with (
            patch("gigaam_transcriber.autoflow.generate_summary", new_callable=AsyncMock) as gs,
            patch("gigaam_transcriber.autoflow.generate_mindmap_markdown") as gm,
        ):
            gs.return_value = "# Summary"
            app = FastAPI()
            app.include_router(autoflow_router.router)
            app.state.transcriber = transcriber

            payload = os.urandom(128)
            messages = []
            with TestClient(app) as client:
                with client.websocket_connect("/api/autoflow/ws") as ws:
                    ws.send_text(auth_frame())
                    messages.append(ws.receive_json())
                    ws.send_text(json.dumps({
                        "type": "meta", "bytes": len(payload), "filename": "a.wav",
                        "include_summary": False, "include_mindmap": False,
                    }))
                    messages.append(ws.receive_json())
                    ws.send_bytes(payload)
                    while True:
                        msg = ws.receive_json()
                        messages.append(msg)
                        if msg["type"] in ("complete", "error"):
                            break

        gs.assert_not_called()
        gm.assert_not_called()
        complete = messages[-1]
        assert complete["type"] == "complete"
        result = complete["result"]
        assert "summary" not in result
        assert "mindmap_md" not in result
        assert result["transcription"]["duration"] == 60.0

        skipped = [m for m in messages if m.get("stage") == "skipped"]
        assert {s.get("skipped_stage") for s in skipped} == {"summary", "insights", "mindmap"}

        seqs = [m["seq"] for m in messages]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            events = (await session.execute(select(UsageEvent).where(UsageEvent.user_id == USER_ID))).scalars().all()
        by_type = {}
        for ev in events:
            by_type.setdefault(ev.event_type, []).append(ev.value)
        assert by_type.get("transcription_minutes") == [1.0]  # 60s → 1 min
        assert by_type.get("file_upload") == [1.0]

        await engine.dispose()
        assert ds_files(ws_env) == []
