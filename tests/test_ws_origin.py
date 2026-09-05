"""Origin-header validation for the shared WS protocol (SEC-M5).

Covers: same-origin allowed, foreign origin rejected with 4400 before
first-frame auth, port mismatch rejected, ``Origin: null`` rejected,
missing Origin (non-browser client) proceeds to auth, and
``WS_ALLOWED_ORIGINS`` allowlist entry allowed.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

import gigaam_transcriber.ws_protocol as ws_protocol
from gigaam_transcriber.auth import create_access_token


def make_user_row():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111", email="u@example.com", role="user", is_active=True
    )


def patch_db(monkeypatch):
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: make_user_row())
    )

    @contextlib.asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(ws_protocol, "async_session_factory", factory)


def make_app() -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws")
    async def origin_ws(ws: WebSocket):
        async with ws_protocol.ws_db_session() as db:
            identity = await ws_protocol.authenticate_websocket(ws, db)
        if identity is None:
            return
        await ws.send_text(json.dumps({"type": "auth_ok", "protocol": 1}))

    return app


def auth_frame() -> str:
    token = create_access_token("11111111-1111-1111-1111-111111111111", "user")
    return json.dumps({"type": "auth", "token": token, "protocol": 1})


def expect_close(ws, code: int) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            ws.receive_text()
    assert excinfo.value.code == code


@pytest.fixture(autouse=True)
def clean_origin_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "2")
    return str(tmp_path)


class TestWsOriginPolicy:
    def test_same_origin_allowed(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "http://testserver"}
            ) as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

    def test_same_origin_https_over_wss_allowed(self, monkeypatch):
        fake_ws = SimpleNamespace(
            headers={"origin": "https://testserver", "host": "testserver"},
            url=SimpleNamespace(scheme="wss"),
        )
        assert ws_protocol._origin_allowed(fake_ws) is True

    def test_same_origin_explicit_port_match_allowed(self, monkeypatch):
        fake_ws = SimpleNamespace(
            headers={"origin": "http://testserver:8080", "host": "testserver:8080"},
            url=SimpleNamespace(scheme="ws"),
        )
        assert ws_protocol._origin_allowed(fake_ws) is True

    def test_foreign_origin_rejected_4400_before_auth(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "https://evil.example"}
            ) as ws:
                expect_close(ws, 4400)

    def test_port_mismatch_origin_rejected_4400(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "http://testserver:9999"}
            ) as ws:
                expect_close(ws, 4400)

    def test_null_origin_rejected_4400(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "null"}
            ) as ws:
                expect_close(ws, 4400)

    def test_missing_origin_proceeds_to_auth(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

    def test_missing_origin_bad_token_still_rejected_4401(self, monkeypatch):
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "bogus", "protocol": 1}))
                expect_close(ws, 4401)

    def test_allowlist_entry_allowed(self, monkeypatch):
        monkeypatch.setenv("WS_ALLOWED_ORIGINS", "https://trusted.example, https://other.example")
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "https://trusted.example"}
            ) as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

    def test_allowlist_trailing_slash_normalised(self, monkeypatch):
        monkeypatch.setenv("WS_ALLOWED_ORIGINS", "https://trusted.example/")
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "https://trusted.example"}
            ) as ws:
                ws.send_text(auth_frame())
                assert ws.receive_json()["type"] == "auth_ok"

    def test_foreign_origin_not_in_allowlist_rejected(self, monkeypatch):
        monkeypatch.setenv("WS_ALLOWED_ORIGINS", "https://trusted.example")
        patch_db(monkeypatch)
        with TestClient(make_app()) as client:
            with client.websocket_connect(
                "/ws", headers={"Origin": "https://evil.example"}
            ) as ws:
                expect_close(ws, 4400)
