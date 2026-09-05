"""Task 10 — bounded live-hints pipeline tests.

Covers the queue architecture (receive / dispatch / processor), deterministic
shutdown, non-blocking receive under saturation, and the env-tunable bounds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gigaam_transcriber.live_hints_service as live_hints_service
import gigaam_transcriber.ws_protocol as ws_protocol
import routers.live_hints as live_hints_router
from gigaam_transcriber.auth import create_access_token

USER_ID = "11111111-1111-1111-1111-111111111111"


def make_user_row():
    return SimpleNamespace(id=USER_ID, email="u@example.com", role="user", is_active=True)


class FakeWebSocket:
    """Minimal ASGI websocket for driving the router inside our own loop.

    ``stop_until`` (optional) is polled once the scripted frames are exhausted;
    the session ends with a clean disconnect when it returns True.
    """

    def __init__(self, incoming: list[dict], stop_until=None) -> None:
        self._incoming = list(incoming)
        self._stop_until = stop_until
        self.query_params: dict[str, str] = {}
        self.sent: list[str] = []
        self.accepted = False
        self.close_code: int | None = None
        self._release = asyncio.Event()

    def client_disconnect(self) -> None:
        self._release.set()

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        await asyncio.sleep(0)
        if self._incoming:
            return self._incoming.pop(0)
        while not self._release.is_set():
            if self._stop_until is not None and self._stop_until():
                self._release.set()
                break
            await asyncio.sleep(0.005)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code = code


def auth_msg(token: str | None = None) -> dict:
    return {
        "type": "websocket.receive",
        "text": json.dumps(
            {"type": "auth", "token": token or create_access_token(USER_ID, "user"), "protocol": 1}
        ),
    }


def audio_msg(payload: bytes, source_tag: int = 1) -> dict:
    return {"type": "websocket.receive", "bytes": bytes([source_tag]) + payload}


def text_msg(obj: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(obj)}


@pytest.fixture()
def patched_deps(monkeypatch):
    """Patch auth DB, settings DB and heavy clients at the router boundary."""
    adapter = MagicMock(name="audio_adapter")
    adapter.close = AsyncMock()
    monkeypatch.setattr(live_hints_router, "AudioAdapter", MagicMock(return_value=adapter))

    monkeypatch.setattr(live_hints_router, "LLMClient", MagicMock())
    monkeypatch.setattr(live_hints_router, "LLMClientConfig", MagicMock())

    cascade = MagicMock(name="cascade")
    cascade.run = MagicMock(return_value=None)
    monkeypatch.setattr(live_hints_router, "LLMCascade", MagicMock(return_value=cascade))
    monkeypatch.setattr(live_hints_router, "generate_hints", MagicMock(return_value=[]))

    accumulators: list = []
    real_cls = live_hints_router.SessionAccumulator

    def capture_accumulator():
        acc = real_cls()
        accumulators.append(acc)
        return acc

    monkeypatch.setattr(live_hints_router, "SessionAccumulator", capture_accumulator)

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

    return SimpleNamespace(adapter=adapter, cascade=cascade, accumulators=accumulators)


def sent_json(fake: FakeWebSocket) -> list[dict]:
    return [json.loads(s) for s in fake.sent]


class TestBoundedPipeline:
    def test_storm_500_chunks_fast_processor_keeps_up(self, monkeypatch, patched_deps):
        monkeypatch.setenv("LIVE_HINTS_RECV_QUEUE", "512")
        monkeypatch.setenv("LIVE_HINTS_PROCESS_QUEUE", "64")
        monkeypatch.setenv("LIVE_HINTS_MAX_SEGMENTS", "200")

        processed = 0

        async def fake_process(audio: bytes, source: str) -> str:
            nonlocal processed
            processed += 1
            return "цена 100 руб и вопрос?"

        patched_deps.adapter.process_chunk_bytes = AsyncMock(side_effect=fake_process)

        incoming = [auth_msg()] + [audio_msg(b"x" * 64) for _ in range(500)]

        def all_transcripts(fake: FakeWebSocket) -> int:
            return sum(1 for s in fake.sent if '"transcript"' in s)

        fake = FakeWebSocket(incoming, stop_until=lambda: all_transcripts(fake) >= 500)
        t0 = time.monotonic()
        asyncio.run(live_hints_router.live_hints_ws(fake))
        elapsed = time.monotonic() - t0

        assert fake.accepted
        assert processed == 500
        msgs = sent_json(fake)
        transcripts = [m for m in msgs if m["type"] == "transcript"]
        assert len(transcripts) == 500
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        acc = patched_deps.accumulators[-1]
        assert len(acc.prices_mentioned) <= acc.caps.max_prices
        assert len(acc.facts) <= acc.caps.max_facts
        assert elapsed < 30

    def test_saturation_drops_oldest_and_receive_never_blocks(self, monkeypatch, patched_deps, caplog):
        monkeypatch.setenv("LIVE_HINTS_RECV_QUEUE", "4")
        monkeypatch.setenv("LIVE_HINTS_PROCESS_QUEUE", "2")

        async def slow_process(audio: bytes, source: str) -> str:
            await asyncio.sleep(0.05)
            return "текст"

        patched_deps.adapter.process_chunk_bytes = AsyncMock(side_effect=slow_process)

        n = 60
        incoming = [auth_msg()] + [audio_msg(b"y" * 32) for _ in range(n)]
        fake = FakeWebSocket(incoming)
        fake.client_disconnect()

        t0 = time.monotonic()
        asyncio.run(live_hints_router.live_hints_ws(fake))
        elapsed = time.monotonic() - t0

        await_count = patched_deps.adapter.process_chunk_bytes.await_count
        assert await_count <= n
        assert elapsed < n * 0.05, "receive loop blocked on processing"
        drop_warnings = [
            r for r in caplog.records
            if "dropped oldest chunk" in r.getMessage()
        ]
        assert drop_warnings, "overflow must be logged (never silent)"

        bp = [m for m in sent_json(fake) if m["type"] == "status" and m["status"] == "backpressure"]
        assert bp, "overflow must be surfaced as a backpressure status event"
        last = bp[-1]
        assert last["dropped"] >= 1
        assert set(last["queues"]) == {"recv", "process"}
        assert last["accumulators"]["segments"] <= last["caps"]["max_segments"]

    def test_shutdown_leaves_no_orphan_tasks(self, monkeypatch, patched_deps):
        patched_deps.adapter.process_chunk_bytes = AsyncMock(return_value="ok")

        async def scenario() -> set:
            baseline = set(asyncio.all_tasks()) - {asyncio.current_task()}
            incoming = [auth_msg()] + [audio_msg(b"z" * 16) for _ in range(10)]
            fake = FakeWebSocket(incoming)
            fake.client_disconnect()
            await live_hints_router.live_hints_ws(fake)
            await asyncio.sleep(0.2)
            return set(asyncio.all_tasks()) - {asyncio.current_task()} - baseline

        leftovers = asyncio.run(scenario())
        assert leftovers == set()
        patched_deps.adapter.close.assert_awaited()

    def test_hint_request_processed_off_receive_loop(self, monkeypatch, patched_deps):
        monkeypatch.setenv("LIVE_HINTS_MAX_SEGMENTS", "200")
        patched_deps.adapter.process_chunk_bytes = AsyncMock(return_value="какая цена?")

        incoming = [
            auth_msg(),
            audio_msg(b"a" * 8),
            text_msg({"type": "session_config", "template_key": "negotiation"}),
            text_msg({"type": "hint_request"}),
        ]
        fake = FakeWebSocket(incoming, stop_until=lambda: patched_deps.cascade.run.called)
        asyncio.run(live_hints_router.live_hints_ws(fake))

        msgs = sent_json(fake)
        assert any(m["type"] == "status" and m["status"] == "ready" for m in msgs)
        assert patched_deps.cascade.run.called
        assert any(m["type"] == "transcript" for m in msgs)


class TestAccumulatorCaps:
    def test_histories_are_bounded_deques_with_env_caps(self, monkeypatch):
        monkeypatch.setenv("LIVE_HINTS_MAX_FACTS", "3")
        monkeypatch.setenv("LIVE_HINTS_MAX_HINTS", "5")
        monkeypatch.setenv("LIVE_HINTS_MAX_PRICES", "2")
        from gigaam_transcriber.accumulator import SessionAccumulator

        acc = SessionAccumulator()
        assert acc.facts.maxlen == 3
        assert acc.hint_history.maxlen == 5
        assert acc.prices_mentioned.maxlen == 2

    def test_eviction_is_oldest_out(self):
        import time as _time
        from gigaam_transcriber.accumulator import SessionAccumulator, AccumulatorCaps

        acc = SessionAccumulator(caps=AccumulatorCaps(max_facts=2, max_hints=10, max_key_topics=10, max_objections=10, max_prices=2))
        acc.add_fact("first", "entity", 1.0)
        acc.add_fact("second", "entity", 2.0)
        acc.add_fact("third", "entity", 3.0)
        assert [f.text for f in acc.facts] == ["second", "third"]

        acc.prices_mentioned.append("100 руб")
        acc.prices_mentioned.append("200 руб")
        acc.prices_mentioned.append("300 руб")
        assert list(acc.prices_mentioned) == ["200 руб", "300 руб"]

        assert acc.sizes() == {
            "facts": 2, "hint_history": 0, "key_topics": 0,
            "objections_raised": 0, "prices_mentioned": 2,
        }
        assert _time.time() >= 0

    def test_get_hint_history_returns_recent_records(self):
        from gigaam_transcriber.accumulator import SessionAccumulator
        from gigaam_transcriber.hint_typology import Hint, HintType, HintPriority

        acc = SessionAccumulator()
        for i in range(7):
            acc.add_hint(Hint(
                hint_id=f"h{i}", type=HintType.TACTICAL, text=f"t{i}",
                priority=HintPriority.MEDIUM, rationale="", timestamp=float(i),
            ))
        recent = acc.get_hint_history(3)
        assert [r.hint_id for r in recent] == ["h4", "h5", "h6"]
        assert acc.get_hint_history(0) == []


class TestRetrySingleOwner:
    def test_adapter_passes_policy_to_provider_no_outer_retry(self, monkeypatch):
        captured: dict = {}
        calls: list[int] = []

        async def failing_transcribe(audio, filename, language=None):
            calls.append(1)
            raise live_hints_service.ASRError("boom")

        provider = MagicMock()
        provider.transcribe_raw = AsyncMock(side_effect=failing_transcribe)

        def fake_factory(preference=None, fallback=True, max_retries=None):
            captured["max_retries"] = max_retries
            return provider

        monkeypatch.setattr(live_hints_service, "get_asr_provider", fake_factory)
        monkeypatch.setenv("LIVE_HINTS_ASR_ATTEMPTS", "2")

        import io as _io

        buf = _io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 160)
        monkeypatch.setattr(
            live_hints_service.AudioAdapter, "_decode_to_wav", lambda self, raw: buf.getvalue()
        )

        adapter = live_hints_service.AudioAdapter()

        async def run():
            await adapter.process_chunk_bytes(b"ab", "mic")

        with pytest.raises(live_hints_service.ASRError):
            asyncio.run(run())

        assert captured["max_retries"] == 1
        assert len(calls) == 1

    def test_provider_retries_exactly_configured_attempts(self, monkeypatch):
        import httpx

        from gigaam_transcriber.litellm_client import LiteLLMASRClient

        client = LiteLLMASRClient(max_retries=1)
        post = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"text": "ок"},
                ),
            ]
        )
        monkeypatch.setattr(client._client, "post", post)

        async def run():
            return await client.transcribe_raw(b"audio", "a.wav")

        assert asyncio.run(run()) == "ок"
        assert post.await_count == 2

    def test_provider_default_retries_unchanged_for_other_consumers(self):
        from gigaam_transcriber.litellm_client import LiteLLMASRClient
        from gigaam_transcriber.mistral_client import MistralASRClient

        assert LiteLLMASRClient()._max_retries == 3
        assert MistralASRClient()._max_retries == 3

    def test_factory_forwards_max_retries(self):
        from gigaam_transcriber.asr_provider import get_asr_provider
        from gigaam_transcriber.litellm_client import LiteLLMASRClient

        provider = get_asr_provider("litellm", fallback=False, max_retries=0)
        assert isinstance(provider, LiteLLMASRClient)
        assert provider._max_retries == 0


class TestChunkValidation:
    def make_wav(self, frames: int, rate: int = 16000) -> bytes:
        import io as _io

        buf = _io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * frames)
        return buf.getvalue()

    def adapter_with(self, monkeypatch, max_chunk_seconds: str):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", max_chunk_seconds)
        provider = MagicMock()
        provider.transcribe_raw = AsyncMock(return_value="текст")
        monkeypatch.setattr(live_hints_service, "get_asr_provider", MagicMock(return_value=provider))
        adapter = live_hints_service.AudioAdapter()
        return adapter, provider

    def run_chunk(self, adapter, wav: bytes) -> str:
        def fake_run(args, **kwargs):
            dst = args[-1]
            with open(dst, "wb") as f:
                f.write(wav)
            return SimpleNamespace(returncode=0, stderr=b"")

        import unittest.mock

        adapter_module_patch = unittest.mock.patch.object(
            live_hints_service.subprocess, "run", fake_run
        )

        async def go():
            with adapter_module_patch:
                return await adapter.process_chunk_bytes(b"webm", "mic")

        return asyncio.run(go())

    def test_oversize_duration_rejected_and_counted(self, monkeypatch):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", "0.01")
        adapter, provider = self.adapter_with(monkeypatch, "0.01")
        wav = self.make_wav(frames=1600)  # 1600 frames @ 16 kHz = 0.1 s

        assert self.run_chunk(adapter, wav) == ""
        assert adapter.invalid_chunks == 1
        provider.transcribe_raw.assert_not_awaited()

    def test_reasonable_chunk_accepted(self, monkeypatch):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", "30")
        adapter, provider = self.adapter_with(monkeypatch, "30")
        wav = self.make_wav(frames=1600)

        assert self.run_chunk(adapter, wav) == "текст"
        assert adapter.invalid_chunks == 0
        provider.transcribe_raw.assert_awaited_once()

    def test_zero_frames_treated_as_silent_not_invalid(self, monkeypatch):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", "30")
        adapter, provider = self.adapter_with(monkeypatch, "30")
        wav = self.make_wav(frames=0)

        assert self.run_chunk(adapter, wav) == ""
        assert adapter.invalid_chunks == 0
        provider.transcribe_raw.assert_not_awaited()

    def test_garbage_wav_rejected_not_raised(self, monkeypatch):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", "30")
        adapter, provider = self.adapter_with(monkeypatch, "30")

        assert self.run_chunk(adapter, b"not a wav at all") == ""
        assert adapter.invalid_chunks == 1
        provider.transcribe_raw.assert_not_awaited()

    def test_status_distinguishes_invalid_from_silent(self, monkeypatch, patched_deps):
        monkeypatch.setenv("LIVE_HINTS_MAX_CHUNK_SECONDS", "0.001")

        adapter = MagicMock(name="audio_adapter")
        adapter.invalid_chunks = 0
        adapter.close = AsyncMock()

        async def fake_process(audio: bytes, source: str) -> str:
            adapter.invalid_chunks += 1
            return ""

        adapter.process_chunk_bytes = AsyncMock(side_effect=fake_process)
        monkeypatch.setattr(live_hints_router, "AudioAdapter", MagicMock(return_value=adapter))

        incoming = [auth_msg(), audio_msg(b"garbage")]

        def got_invalid_status() -> bool:
            return any('"invalid_chunk"' in s_ for s_ in fake.sent)

        fake = FakeWebSocket(incoming, stop_until=got_invalid_status)
        asyncio.run(live_hints_router.live_hints_ws(fake))

        msgs = sent_json(fake)
        invalid = [m for m in msgs if m["type"] == "status" and m["status"] == "invalid_chunk"]
        assert invalid and invalid[0]["invalid_chunks"] >= 1



    def test_ffmpeg_runs_in_worker_thread(self, monkeypatch, tmp_path):
        import os as _os

        thread_ids: list[int] = []

        def fake_run(args, **kwargs):
            thread_ids.append(threading.get_ident())
            dst = args[-1]
            with wave.open(dst, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 160)
            return SimpleNamespace(returncode=0, stderr=b"")

        monkeypatch.setattr(live_hints_service.subprocess, "run", fake_run)

        provider = MagicMock()
        provider.transcribe_raw = AsyncMock(return_value="привет")
        monkeypatch.setattr(live_hints_service, "get_asr_provider", MagicMock(return_value=provider))

        adapter = live_hints_service.AudioAdapter()
        loop_thread = threading.get_ident()

        async def run():
            return await adapter.process_chunk_bytes(b"fake-webm", "mic")

        text = asyncio.run(run())
        assert text == "привет"
        assert thread_ids and all(tid != loop_thread for tid in thread_ids)
        provider.transcribe_raw.assert_awaited_once()
        leftovers = [p for p in tmp_path.iterdir()]
        assert leftovers == []

    def test_ffmpeg_failure_returns_empty_without_raise(self, monkeypatch):
        monkeypatch.setattr(
            live_hints_service.subprocess,
            "run",
            MagicMock(return_value=SimpleNamespace(returncode=1, stderr=b"bad")),
        )
        provider = MagicMock()
        provider.transcribe_raw = AsyncMock()
        monkeypatch.setattr(live_hints_service, "get_asr_provider", MagicMock(return_value=provider))

        adapter = live_hints_service.AudioAdapter()

        async def run():
            return await adapter.process_chunk_bytes(b"garbage", "mic")

        assert asyncio.run(run()) == ""
        provider.transcribe_raw.assert_not_awaited()

    def test_tempfiles_removed_after_decode(self, monkeypatch):
        import glob
        import tempfile as _tempfile

        def fake_run(args, **kwargs):
            dst = args[-1]
            with wave.open(dst, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 160)
            return SimpleNamespace(returncode=0, stderr=b"")

        monkeypatch.setattr(live_hints_service.subprocess, "run", fake_run)
        provider = MagicMock()
        provider.transcribe_raw = AsyncMock(return_value="ok")
        monkeypatch.setattr(live_hints_service, "get_asr_provider", MagicMock(return_value=provider))

        tmpdir = _tempfile.gettempdir()
        before = set(glob.glob(f"{tmpdir}/tmp*.webm")) | set(glob.glob(f"{tmpdir}/tmp*.wav"))

        async def run():
            return await live_hints_service.AudioAdapter().process_chunk_bytes(b"abc", "mic")

        asyncio.run(run())

        time.sleep(0.05)
        after = set(glob.glob(f"{tmpdir}/tmp*.webm")) | set(glob.glob(f"{tmpdir}/tmp*.wav"))
        assert not (after - before)
