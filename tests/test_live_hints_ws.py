"""Live-hints WS router migration tests (Task 8, slice 3).

Verifies the router uses the shared ws_protocol (first-frame auth, binary
[tag][webm] audio frames through StreamSession, outbound channel ordering)
while keeping the hint-generation loop semantics intact.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gigaam_transcriber.ws_protocol as ws_protocol
import routers.live_hints as live_hints_router
from gigaam_transcriber.auth import create_access_token

USER_ID = "11111111-1111-1111-1111-111111111111"


def make_user_row():
    return SimpleNamespace(id=USER_ID, email="u@example.com", role="user", is_active=True)


@pytest.fixture()
def ws_env(monkeypatch):
    monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("WS_MAX_FRAME_BYTES", str(64 * 1024))
    monkeypatch.setenv("WS_MAX_TOTAL_BYTES", str(256 * 1024))


def make_app(monkeypatch, *, asr_texts=None):
    asr_texts = asr_texts if asr_texts is not None else ["привет мир"]

    adapter = MagicMock(name="audio_adapter")
    adapter.process_chunk_bytes = AsyncMock(side_effect=list(asr_texts) + [""] * 10)
    adapter.close = AsyncMock()
    monkeypatch.setattr(live_hints_router, "AudioAdapter", MagicMock(return_value=adapter))
    monkeypatch.setattr(live_hints_router, "LLMClient", MagicMock())
    monkeypatch.setattr(live_hints_router, "LLMClientConfig", MagicMock())
    monkeypatch.setattr(live_hints_router, "generate_hints", MagicMock(return_value=[]))

    cascade = MagicMock(name="cascade")
    cascade.run = MagicMock(return_value=None)
    monkeypatch.setattr(live_hints_router, "LLMCascade", MagicMock(return_value=cascade))

    auth_session = MagicMock()
    auth_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
    )

    settings_session = MagicMock()
    settings_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: None)
    )

    @contextlib.asynccontextmanager
    async def auth_factory():
        yield auth_session

    @contextlib.asynccontextmanager
    async def settings_factory():
        yield settings_session

    monkeypatch.setattr(ws_protocol, "async_session_factory", auth_factory)
    monkeypatch.setattr(live_hints_router, "async_session_factory", settings_factory)

    app = FastAPI()
    app.include_router(live_hints_router.router)
    app.state.adapter = adapter
    app.state.cascade = cascade
    return app


def auth_frame(token: str | None = None) -> str:
    return json.dumps(
        {"type": "auth", "token": token or create_access_token(USER_ID, "user"), "protocol": 1}
    )


def expect_close(ws, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            ws.receive_text()
    assert excinfo.value.code == code


class TestLiveHintsWsProtocol:
    def test_auth_config_audio_transcript_feedback(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, asr_texts=["привет мир", "ответ оппонента"])
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

                audio = b"\x1a\x45\xdf\xa3fake-webm"
                ws.send_bytes(bytes([ws_protocol.TAG_FOR_SOURCE["mic"]]) + audio)
                reply = ws.receive_json()
                assert reply["type"] == "transcript"
                assert reply["text"] == "привет мир"
                assert reply["speaker"] == "user"

                ws.send_bytes(bytes([ws_protocol.TAG_FOR_SOURCE["tab"]]) + audio)
                while True:
                    reply2 = ws.receive_json()
                    if reply2.get("type") == "transcript" and reply2.get("speaker") == "opponent":
                        break

                ws.send_text(json.dumps({"type": "session_config", "template_key": "negotiation"}))
                assert ws.receive_json()["type"] == "status"

                ws.send_text(json.dumps({"type": "hint_feedback", "hint_id": "h1", "rating": "like"}))
                ack = ws.receive_json()
                assert ack["type"] == "feedback_ack"

        app.state.adapter.process_chunk_bytes.assert_any_call(audio, "mic")
        app.state.adapter.process_chunk_bytes.assert_any_call(audio, "tab")
        assert "seq" in reply and "seq" in reply2 and reply2["seq"] > reply["seq"]
        app.state.adapter.close.assert_awaited()

    def test_query_token_rejected_4400(self, ws_env, monkeypatch):
        app = make_app(monkeypatch)
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/api/live-hints/ws?token={create_access_token(USER_ID, 'user')}"
            ) as ws:
                expect_close(ws, 4400)
        app.state.adapter.process_chunk_bytes.assert_not_called()

    def test_auth_failure_4401(self, ws_env, monkeypatch):
        app = make_app(monkeypatch)
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text("garbage")
                expect_close(ws, 4401)

    def test_unknown_text_type_error_message_no_close(self, ws_env, monkeypatch):
        app = make_app(monkeypatch)
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "wat"}))
                err = ws.receive_json()
                assert err["type"] == "error" and err["code"] == "unknown_type"

                ws.send_text(json.dumps({"type": "session_config", "template_key": "meeting"}))
                assert ws.receive_json()["type"] == "status"

    def test_silent_chunk_status(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, asr_texts=[""])
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "session_config", "template_key": "meeting"}))
                ws.receive_json()
                ws.send_bytes(b"\x01" + b"silence")
                status = ws.receive_json()
                assert status["type"] == "status" and status["status"] == "silent_chunk"

    def test_oversized_audio_frame_4413(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_MAX_FRAME_BYTES", "64")
        app = make_app(monkeypatch)
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_bytes(b"\x01" + b"x" * 200)
                expect_close(ws, 4413)
        app.state.adapter.process_chunk_bytes.assert_not_called()

    def test_asr_error_retries_then_error_message(self, ws_env, monkeypatch):
        from gigaam_transcriber.exceptions import ASRError

        adapter = MagicMock(name="audio_adapter")
        adapter.process_chunk_bytes = AsyncMock(side_effect=ASRError("asr down"))
        adapter.close = AsyncMock()
        monkeypatch.setattr(live_hints_router, "AudioAdapter", MagicMock(return_value=adapter))
        monkeypatch.setattr(live_hints_router, "LLMClient", MagicMock())
        monkeypatch.setattr(live_hints_router, "LLMClientConfig", MagicMock())
        monkeypatch.setattr(live_hints_router, "LLMCascade", MagicMock())

        auth_session = MagicMock()
        auth_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
        )
        settings_session = MagicMock()
        settings_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None)
        )

        @contextlib.asynccontextmanager
        async def auth_factory():
            yield auth_session

        @contextlib.asynccontextmanager
        async def settings_factory():
            yield settings_session

        monkeypatch.setattr(ws_protocol, "async_session_factory", auth_factory)
        monkeypatch.setattr(live_hints_router, "async_session_factory", settings_factory)

        app = FastAPI()
        app.include_router(live_hints_router.router)
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "session_config", "template_key": "meeting"}))
                ws.receive_json()
                ws.send_bytes(b"\x01" + b"noise")
                err = ws.receive_json()
                assert err["type"] == "error" and err["code"] == "asr"
        assert adapter.process_chunk_bytes.await_count == 3
