"""Disconnect-vs-cancel semantics for the autoflow WebSocket.

Quick-win #2 regression coverage (docs/design/background-jobs.md):

* a transport disconnect mid-work must NOT cancel the work — it continues
  detached and the result is persisted via ``_persist_autoflow_result``;
* an explicit ``{"type": "cancel"}`` control frame still cancels.
"""

import asyncio
import contextlib
import json
import os
import threading
import time
from asyncio import sleep as asyncio_sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient

import routers.autoflow as autoflow_router
from gigaam_transcriber.autoflow import AutoflowStage, StageEvent

from tests.test_autoflow_ws import (
    USER_ID,
    auth_frame,
    make_app,
    make_result,
    ws_env,  # noqa: F401  (fixture re-export)
)

# TestClient runs the app loop in a background thread; threading primitives
# are the only safe way to coordinate it with the main test thread.
_RELEASE = threading.Event()


def _wait_for(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _blocking_result():
    tr = SimpleNamespace(
        text="привет мир",
        language="ru",
        duration=2.0,
        segments=[
            SimpleNamespace(text="привет мир", start=0.0, end=2.0, speaker=None, confidence=None)
        ],
    )
    return make_result(
        transcription_result=tr,
        summary_text="сводка",
        mindmap_md="## карта",
        action_items=["задача"],
        suggested_steps=["шаг"],
    )


def _start_session_and_upload(ws):
    ws.send_text(auth_frame())
    assert ws.receive_json()["type"] == "auth_ok"
    ws.send_text(
        json.dumps(
            {
                "type": "meta",
                "bytes": 1024,
                "filename": "meeting.wav",
                "template_key": "meeting",
                "diarization_mode": "none",
            }
        )
    )
    assert ws.receive_json()["type"] == "ack"
    ws.send_bytes(os.urandom(1024))


class TestDisconnectVsCancel:
    def test_disconnect_does_not_cancel_and_persists(self, ws_env, monkeypatch):
        _RELEASE.clear()
        started = threading.Event()

        async def fake_run_autoflow(**kwargs):
            started.set()
            cb = kwargs.get("progress_callback")
            if cb:
                cb(StageEvent(AutoflowStage.TRANSCRIBE, 0.5, "работаю"))
            while not _RELEASE.is_set():
                await asyncio_sleep(0.01)
            return _blocking_result()

        app = make_app(monkeypatch, ws_env)
        monkeypatch.setattr(autoflow_router, "run_autoflow", fake_run_autoflow)

        persisted = []

        async def fake_persist(app_ref, user_id, result, session_id):
            persisted.append((user_id, result))

        monkeypatch.setattr(autoflow_router, "_persist_autoflow_result", fake_persist)

        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                _start_session_and_upload(ws)
                # wait for the first progress event, then drop the connection
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "progress":
                        break
            # exiting the context closes the WS = transport disconnect

            assert _wait_for(started.is_set), "run_autoflow never started"

            _RELEASE.set()
            assert _wait_for(lambda: bool(persisted)), (
                "detached autoflow result was not persisted after disconnect"
            )

            user_id, result = persisted[0]
            assert user_id == USER_ID
            assert result.transcription_result.text == "привет мир"

    def test_explicit_cancel_still_cancels(self, ws_env, monkeypatch):
        _RELEASE.clear()

        async def fake_run_autoflow(**kwargs):
            while not _RELEASE.is_set():
                await asyncio_sleep(0.01)
            return _blocking_result()

        app = make_app(monkeypatch, ws_env)
        monkeypatch.setattr(autoflow_router, "run_autoflow", fake_run_autoflow)

        persisted = []

        async def fake_persist(app_ref, user_id, result, session_id):
            persisted.append((user_id, result))

        monkeypatch.setattr(autoflow_router, "_persist_autoflow_result", fake_persist)

        with TestClient(app) as client:
            with client.websocket_connect("/api/autoflow/ws") as ws:
                _start_session_and_upload(ws)
                ws.send_text(json.dumps({"type": "cancel"}))
                # server closes the socket after cancel; context exit is fine

        _RELEASE.set()
        time.sleep(0.1)
        assert persisted == [], "cancelled run must not be persisted"

    def test_persist_writes_saved_transcription(self, ws_env, monkeypatch):
        """_persist_autoflow_result happy path with a mocked session."""
        captured_rows = {}

        class FakeSession:
            def add(self, obj):
                captured_rows.setdefault("obj", obj)

            async def commit(self):
                captured_rows.setdefault("committed", True)

        @contextlib.asynccontextmanager
        async def fake_session():
            yield FakeSession()

        monkeypatch.setattr(autoflow_router, "async_session_factory", fake_session)

        class FakeResult:
            transcription_result = SimpleNamespace(
                text="текст",
                language="ru",
                duration=5.0,
                segments=[
                    SimpleNamespace(
                        text="текст", start=0.0, end=5.0, speaker="Спикер №1", confidence=0.9
                    )
                ],
            )
            summary_text = "сводка"
            mindmap_md = "карта"
            action_items = ["пункт"]
            suggested_steps = ["шаг 1"]

        obj = asyncio.run(
            autoflow_router._persist_autoflow_result(None, USER_ID, FakeResult(), "sess-1")
        )
        assert captured_rows["obj"] is obj
        assert captured_rows["committed"]
        assert obj.full_text == "текст"
        assert obj.title.startswith("Autoflow ")
        assert obj.segments_json[0]["speaker"] == "Спикер №1"
        assert "# Сводка" in obj.analysis_text
        assert "# Задачи" in obj.analysis_text
        assert obj.duration == 5.0

    def test_persist_skips_when_no_transcription(self, ws_env, monkeypatch):
        """No transcription_result → nothing to persist, no crash."""

        @contextlib.asynccontextmanager
        async def fake_session():
            raise AssertionError("session must not be opened")

        monkeypatch.setattr(autoflow_router, "async_session_factory", fake_session)

        class EmptyResult:
            transcription_result = None

        obj = asyncio.run(
            autoflow_router._persist_autoflow_result(None, USER_ID, EmptyResult(), "sess-2")
        )
        assert obj is None
