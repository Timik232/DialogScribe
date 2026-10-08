"""Protocol tests for gigaam_transcriber.ws_protocol (Task 8).

Covers: auth timeout 4408, malformed JSON 4401, unknown protocol 4400,
audio-before-auth 4401, query-token downgrade 4400, inactive user 4403,
unknown user 4401, expired/invalid token 4401, wrong declared size 4413,
oversized frame 4413, cumulative over cap 4413 (+tempfile removed),
disconnect cleanup, and the happy path (meta ack, monotonic seq, terminal last).
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

import gigaam_transcriber.ws_protocol as ws_protocol
from gigaam_transcriber.auth import JWT_ALGORITHM, JWT_SECRET, create_access_token


def make_user_row(*, active: bool = True, user_id: str = "11111111-1111-1111-1111-111111111111"):
    return SimpleNamespace(id=user_id, email="u@example.com", role="user", is_active=active)


def patch_db(monkeypatch, user):
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: user))

    @contextlib.asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(ws_protocol, "async_session_factory", factory)


def make_app(tmp_dir: str) -> FastAPI:
    app = FastAPI()
    app.state.channels = []
    app.state.handler_errors = []

    @app.websocket("/ws")
    async def protocol_ws(ws: WebSocket):
        async with ws_protocol.ws_db_session() as db:
            identity = await ws_protocol.authenticate_websocket(ws, db)
        if identity is None:
            return
        channel = ws_protocol.OutboundChannel(ws)
        await channel.start()
        app.state.channels.append(channel)
        upload = ws_protocol.BinaryUpload(ws, channel=channel, tmp_dir=tmp_dir)
        try:
            await channel.send({"type": "auth_ok", "protocol": ws_protocol.WS_PROTOCOL_VERSION})
            meta = await upload.receive_meta()
            if meta is None:
                return
            channel.emit({"type": "status", "stage": "upload_complete"})
            if not await upload.receive_binary():
                return
            channel.emit({"type": "progress", "stage": "processing", "progress": 0.5})
            await channel.close_terminal({"type": "complete", "stage": "complete"})
        except Exception as exc:  # pragma: no cover - recorded for diagnostics
            app.state.handler_errors.append(repr(exc))
            raise
        finally:
            if upload.path:
                ws_protocol._discard(upload.path)
                upload.path = None
            await channel.shutdown()

    return app


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("WS_MAX_FRAME_BYTES", str(64 * 1024))
    monkeypatch.setenv("WS_MAX_TOTAL_BYTES", str(256 * 1024))
    return str(tmp_path)


def token_for(user_id: str = "11111111-1111-1111-1111-111111111111") -> str:
    return create_access_token(user_id, "user")


def expired_token(user_id: str = "11111111-1111-1111-1111-111111111111") -> str:
    payload = {
        "sub": user_id,
        "role": "user",
        "type": "access",
        "exp": datetime.utcnow() - timedelta(hours=1),
        "iat": datetime.utcnow() - timedelta(hours=2),
    }
    return jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def auth_frame(token: str, protocol: int = 1) -> str:
    return json.dumps({"type": "auth", "token": token, "protocol": protocol})


def expect_close(ws, code: int) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            ws.receive_text()
    assert excinfo.value.code == code


def ds_files(tmp_dir: str) -> list[str]:
    return [p for p in os.listdir(tmp_dir) if p.startswith("ds_ws_")]


class TestAuthHandshake:
    def test_timeout_closes_4408(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_AUTH_TIMEOUT_SECONDS", "0.3")
        patch_db(monkeypatch, make_user_row())
        app = make_app(ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                expect_close(ws, 4408)
        assert ds_files(ws_env) == []

    def test_malformed_json_closes_4401(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text("this is not json")
                expect_close(ws, 4401)

    def test_binary_first_frame_closes_4401(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_bytes(b"\x01audio-before-auth")
                expect_close(ws, 4401)

    def test_unknown_protocol_version_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for(), protocol=2))
                expect_close(ws, 4400)

    def test_query_token_downgrade_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect(f"/ws?token={token_for()}") as ws:
                expect_close(ws, 4400)

    def test_unknown_user_closes_4401(self, ws_env, monkeypatch):
        patch_db(monkeypatch, None)
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                expect_close(ws, 4401)

    def test_inactive_user_closes_4403(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row(active=False))
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                expect_close(ws, 4403)

    def test_expired_token_closes_4401(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(expired_token()))
                expect_close(ws, 4401)

    def test_refresh_type_token_closes_4401(self, ws_env, monkeypatch):
        from gigaam_transcriber.auth import create_refresh_token

        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(create_refresh_token("11111111-1111-1111-1111-111111111111")))
                expect_close(ws, 4401)

    def test_wrong_first_frame_type_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(json.dumps({"type": "meta", "bytes": 10}))
                expect_close(ws, 4400)


class TestUploadLimits:
    def auth_and_meta(self, client, *, declared: int, filename: str = "a.wav"):
        ws = client.websocket_connect("/ws").__enter__()
        ws.send_text(auth_frame(token_for()))
        ack = ws.receive_json()
        assert ack["type"] == "auth_ok"
        ws.send_text(json.dumps({"type": "meta", "bytes": declared, "filename": filename}))
        return ws

    def test_wrong_declared_size_closes_4413_and_removes_tempfile(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            ws = self.auth_and_meta(client, declared=10)
            ack = ws.receive_json()
            assert ack["type"] == "ack" and ack["meta_ok"] is True and ack["bytes"] == 10
            ws.send_bytes(b"x" * 20)
            expect_close(ws, 4413)
            ws.__exit__(None, None, None)
        assert ds_files(ws_env) == []

    def test_oversized_frame_closes_4413(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_MAX_FRAME_BYTES", "1024")
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            ws = self.auth_and_meta(client, declared=256 * 1024)
            ws.receive_json()
            ws.send_bytes(b"x" * 2048)
            expect_close(ws, 4413)
            ws.__exit__(None, None, None)
        assert ds_files(ws_env) == []

    def test_declared_over_cap_rejected_at_meta(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                assert ws.receive_json()["type"] == "auth_ok"
                ws.send_text(json.dumps({"type": "meta", "bytes": 10 * 1024 * 1024}))
                expect_close(ws, 4413)
        assert ds_files(ws_env) == []

    def test_cumulative_over_cap_closes_4413(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_MAX_TOTAL_BYTES", "2048")
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            ws = self.auth_and_meta(client, declared=2048)
            ws.receive_json()
            ws.send_bytes(b"a" * 1024)
            ws.send_bytes(b"b" * 1500)
            expect_close(ws, 4413)
            ws.__exit__(None, None, None)
        assert ds_files(ws_env) == []

    def test_text_frame_during_upload_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            ws = self.auth_and_meta(client, declared=100)
            ws.receive_json()
            ws.send_text(json.dumps({"type": "session_config"}))
            expect_close(ws, 4400)
            ws.__exit__(None, None, None)
        assert ds_files(ws_env) == []

    def test_meta_bad_shape_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        with TestClient(make_app(ws_env)) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                assert ws.receive_json()["type"] == "auth_ok"
                ws.send_text(json.dumps({"type": "meta", "bytes": "many"}))
                expect_close(ws, 4400)

    def test_disconnect_mid_upload_cleans_tempfile(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        app = make_app(ws_env)
        with TestClient(app) as client:
            ws = self.auth_and_meta(client, declared=4096)
            ws.receive_json()
            ws.send_bytes(b"x" * 100)
            ws.__exit__(None, None, None)
            for _ in range(100):
                if app.state.channels and all(c.done for c in app.state.channels):
                    break
                import time

                time.sleep(0.05)
        assert ds_files(ws_env) == []
        assert app.state.handler_errors == []


class TestHappyPath:
    def test_ack_monotonic_seq_terminal_last(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        app = make_app(ws_env)
        payload = os.urandom(100_000)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                messages = []

                ack = ws.receive_json()
                assert ack["type"] == "auth_ok" and ack["protocol"] == 1

                ws.send_text(json.dumps({"type": "meta", "bytes": len(payload), "filename": "a.wav"}))
                messages.append(ws.receive_json())
                assert messages[-1]["type"] == "ack" and messages[-1]["bytes"] == len(payload)

                chunk = 16 * 1024
                for off in range(0, len(payload), chunk):
                    ws.send_bytes(payload[off : off + chunk])
                while True:
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg["type"] == "complete":
                        break

                seqs = [m["seq"] for m in messages]
                assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
                assert messages[0]["seq"] == 2  # auth_ok was seq 1
                assert messages[-1]["type"] == "complete"
                for m in messages[:-1]:
                    assert m["type"] != "complete"

                from starlette.websockets import WebSocketDisconnect

                with pytest.raises(WebSocketDisconnect) as excinfo:
                    ws.receive_text()
                assert excinfo.value.code in (1000, 1001)
        assert ds_files(ws_env) == []
        assert app.state.handler_errors == []


class TestStreamSession:
    def _stream_app(self, tmp_dir: str) -> FastAPI:
        app = FastAPI()
        app.state.received = []

        @app.websocket("/ws")
        async def stream_ws(ws: WebSocket):
            async with ws_protocol.ws_db_session() as db:
                identity = await ws_protocol.authenticate_websocket(ws, db)
            if identity is None:
                return
            channel = ws_protocol.OutboundChannel(ws)
            await channel.start()
            session = ws_protocol.StreamSession(ws)
            try:
                await channel.send({"type": "auth_ok", "protocol": 1})
                while True:
                    kind, *rest = await session.receive()
                    if kind == "audio":
                        source, audio = rest
                        app.state.received.append((source, len(audio)))
                        await channel.send({"type": "transcript", "text": "ok", "source": source})
                    else:
                        (data,) = rest
                        app.state.received.append(("text", data.get("type")))
                        await channel.send({"type": "status", "status": "ready"})
            except ws_protocol.WsProtocolError:
                pass
            except WebSocketDisconnect:
                pass
            except Exception as exc:  # pragma: no cover
                app.state.handler_errors = getattr(app.state, "handler_errors", []) + [repr(exc)]
                raise
            finally:
                await channel.shutdown()

        return app

    def test_mixed_control_and_audio_frames(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        app = self._stream_app(ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                assert ws.receive_json()["type"] == "auth_ok"

                ws.send_text(json.dumps({"type": "session_config", "template_key": "meeting"}))
                assert ws.receive_json()["type"] == "status"

                ws.send_bytes(bytes([ws_protocol.TAG_FOR_SOURCE["mic"]]) + b"WEBMDATA")
                reply = ws.receive_json()
                assert reply["type"] == "transcript" and reply["source"] == "mic"

                ws.send_bytes(bytes([ws_protocol.TAG_FOR_SOURCE["tab"]]) + b"OTHER")
                assert ws.receive_json()["source"] == "tab"
        assert app.state.received[:3] == [("text", "session_config"), ("mic", 8), ("tab", 5)]

    def test_unknown_source_tag_closes_4400(self, ws_env, monkeypatch):
        patch_db(monkeypatch, make_user_row())
        app = self._stream_app(ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                ws.receive_json()
                ws.send_bytes(b"\x09garbage")
                expect_close(ws, 4400)

    def test_stream_audio_cumulative_cap_4413(self, ws_env, monkeypatch):
        monkeypatch.setenv("WS_MAX_TOTAL_BYTES", "100")
        patch_db(monkeypatch, make_user_row())
        app = self._stream_app(ws_env)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(auth_frame(token_for()))
                ws.receive_json()
                ws.send_bytes(b"\x01" + b"a" * 60)
                ws.receive_json()
                ws.send_bytes(b"\x01" + b"b" * 60)
                expect_close(ws, 4413)


class TestOutboundChannel:
    def test_emit_drops_when_queue_full(self, ws_env):
        import asyncio

        ws = MagicMock()
        ws.send_text = AsyncMock()
        channel = ws_protocol.OutboundChannel(ws, maxsize=1)

        async def run():
            await channel.start()
            try:
                assert channel.emit({"type": "progress"}) is True
                assert channel.emit({"type": "progress"}) is False
                assert channel.seq == 2
            finally:
                await channel.shutdown()

        asyncio.run(run())

    def test_emit_without_start_returns_false(self, ws_env):
        channel = ws_protocol.OutboundChannel(MagicMock())
        assert channel.emit({"type": "progress"}) is False
        assert channel.seq == 0
        channel.shutdown()
