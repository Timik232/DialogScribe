"""Autoflow WS router migration tests (Task 8, slice 2).

Verifies the router uses the shared ws_protocol: first-frame auth (query-token
rejected with 4400), binary upload to tempfile, stage progress via the
outbound channel, and terminal-last ordering with a mocked run_autoflow.
"""

from __future__ import annotations

import contextlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gigaam_transcriber.ws_protocol as ws_protocol
import routers.autoflow as autoflow_router
from gigaam_transcriber.auth import create_access_token
from gigaam_transcriber.autoflow import AutoflowStage, StageEvent

USER_ID = "11111111-1111-1111-1111-111111111111"


def make_user_row():
    return SimpleNamespace(id=USER_ID, email="u@example.com", role="user", is_active=True)


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("WS_MAX_FRAME_BYTES", str(64 * 1024))
    monkeypatch.setenv("WS_MAX_TOTAL_BYTES", str(256 * 1024))
    return str(tmp_path)


def make_result(**overrides):
    base = {
        "errors": [],
        "stage_timings": {"transcription": 1.0},
        "transcription_result": None,
        "summary_text": None,
        "mindmap_md": None,
        "action_items": None,
        "suggested_steps": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_app(monkeypatch, tmp_dir, *, run_result=None, run_exc=None):
    captured = {}

    async def fake_run_autoflow(**kwargs):
        captured.update(kwargs)
        if run_exc is not None:
            raise run_exc
        cb = kwargs.get("progress_callback")
        if cb:
            cb(StageEvent(AutoflowStage.TRANSCRIBE, 0.3, "Транскрибация аудио"))
        return run_result or make_result()

    monkeypatch.setattr(autoflow_router, "run_autoflow", fake_run_autoflow)
    monkeypatch.setattr(autoflow_router, "LLMClient", MagicMock())
    monkeypatch.setattr(autoflow_router, "check_limit", AsyncMock())
    monkeypatch.setattr(autoflow_router, "track_usage", AsyncMock())

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
    )
    session.commit = AsyncMock()

    @contextlib.asynccontextmanager
    async def auth_factory():
        yield session

    @contextlib.asynccontextmanager
    async def work_factory():
        yield session

    monkeypatch.setattr(ws_protocol, "async_session_factory", auth_factory)
    monkeypatch.setattr(autoflow_router, "async_session_factory", work_factory)

    app = FastAPI()
    app.include_router(autoflow_router.router)
    app.state.transcriber = MagicMock(name="transcriber")

    monkeypatch.setattr(
        ws_protocol.BinaryUpload,
        "__init__",
        _patched_upload_init(tmp_dir),
    )
    app.state.captured = captured
    return app


def _patched_upload_init(tmp_dir):
    original = ws_protocol.BinaryUpload.__init__

    def init(self, ws, **kwargs):
        kwargs.setdefault("tmp_dir", tmp_dir)
        original(self, ws, **kwargs)

    return init


def auth_frame(token: str | None = None) -> str:
    return json.dumps(
        {"type": "auth", "token": token or create_access_token(USER_ID, "user"), "protocol": 1}
    )


def expect_close(ws, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            ws.receive_text()
    assert excinfo.value.code == code


def ds_files(tmp_dir: str) -> list[str]:
    return [p for p in os.listdir(tmp_dir) if p.startswith("ds_ws_")]


class TestAutoflowWsHappyPath:
    def test_auth_upload_progress_terminal(self, ws_env, monkeypatch):
        payload = os.urandom(2048)
        app = make_app(monkeypatch, ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

                ws.send_text(
                    json.dumps(
                        {
                            "type": "meta",
                            "bytes": len(payload),
                            "filename": "meeting.wav",
                            "template_key": "meeting",
                            "diarization_mode": "none",
                        }
                    )
                )
                ack = ws.receive_json()
                assert ack["type"] == "ack" and ack["meta_ok"] is True

                ws.send_bytes(payload[:1024])
                ws.send_bytes(payload[1024:])

                messages = []
                while True:
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg.get("type") == "complete":
                        break

        stages = [m.get("stage") for m in messages]
        assert AutoflowStage.UPLOAD.value in stages
        progress = [m for m in messages if m["type"] == "progress"]
        assert progress and progress[0]["stage"] == AutoflowStage.TRANSCRIBE.value

        seqs = [m["seq"] for m in messages]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert messages[-1]["type"] == "complete"
        assert messages[-1]["result"] is not None
        assert messages[-1]["session_id"]

        captured = app.state.captured
        assert captured["user_id"] == USER_ID
        assert captured["file_path"].endswith(".wav")
        assert os.path.exists(captured["file_path"]) is False

        assert ds_files(ws_env) == []

    def test_query_token_rejected_4400(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, ws_env)
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/api/autoflow/ws?token={create_access_token(USER_ID, 'user')}"
            ) as ws:
                expect_close(ws, 4400)

    def test_unsupported_extension_terminal_error(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "meta", "bytes": 16, "filename": "notes.txt"}))
                ws.receive_json()
                msg = ws.receive_json()
                assert msg["type"] == "error" and "Неподдерживаемый формат" in msg["message"]
                with pytest.raises(WebSocketDisconnect) as excinfo:
                    ws.receive_text()
                assert excinfo.value.code == 1000
        assert app.state.captured == {}
        assert ds_files(ws_env) == []

    def test_run_autoflow_exception_terminal_error(self, ws_env, monkeypatch):
        app = make_app(
            monkeypatch, ws_env, run_exc=RuntimeError("transcriber exploded")
        )
        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "meta", "bytes": 8, "filename": "a.wav"}))
                ws.receive_json()
                ws.send_bytes(b"12345678")
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "error":
                        assert "transcriber exploded" in msg["message"]
                        break
        assert ds_files(ws_env) == []

    def test_auth_failure_no_upload(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                ws.send_text("not-json")
                expect_close(ws, 4401)
        assert app.state.captured == {}
        assert ds_files(ws_env) == []
