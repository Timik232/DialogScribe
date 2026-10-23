"""Live-режим (транскрипция без подсказок): hints_enabled=false gates the LLM.

Session with hints_enabled=false must keep transcribing audio but never
invoke the cascade (LLM) nor the template fallback generator.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


class TestLiveModeNoHints:
    def test_hints_disabled_still_transcribes_without_llm(self, ws_env, monkeypatch):
        app = make_app(monkeypatch, asr_texts=["текст без подсказок"])
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

                ws.send_text(
                    json.dumps(
                        {
                            "type": "session_config",
                            "template_key": "meeting",
                            "hints_enabled": False,
                        }
                    )
                )
                assert ws.receive_json()["type"] == "status"

                audio = b"\x1a\x45\xdf\xa3fake-webm"
                ws.send_bytes(bytes([ws_protocol.TAG_FOR_SOURCE["mic"]]) + audio)
                reply = ws.receive_json()
                assert reply["type"] == "transcript"
                assert reply["text"] == "текст без подсказок"

        app.state.cascade.run.assert_not_called()
        live_hints_router.generate_hints.assert_not_called()

    def test_hints_enabled_default_keeps_old_behavior(self, ws_env, monkeypatch):
        """Default (no hints_enabled field) must be accepted as before."""
        app = make_app(monkeypatch)
        with TestClient(app) as client:
            with client.websocket_connect("/api/live-hints/ws") as ws:
                ws.send_text(auth_frame())
                ws.receive_json()
                ws.send_text(json.dumps({"type": "session_config", "template_key": "meeting"}))
                assert ws.receive_json()["type"] == "status"
