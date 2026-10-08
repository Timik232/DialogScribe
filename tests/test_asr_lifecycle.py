"""Task 13 — ASR client ownership, concurrency and cleanup lifecycle tests.

Real async fake providers only (AsyncMock breaks iscoroutinefunction-based
dispatch — known gotcha). Covers CQ-H2: single event loop per operation,
loop-scoped AsyncClients, exactly-once close, bounded concurrency,
cancellation and idempotent cleanup.
"""

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import gigaam_transcriber.litellm_client as litellm_module
from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.asr_provider import ASRProviderBase
from gigaam_transcriber.exceptions import ASRError, EmptyAudioError
from gigaam_transcriber.litellm_client import LiteLLMASRClient
from gigaam_transcriber.mistral_client import MistralASRClient
from gigaam_transcriber import transcriber as transcriber_module
from gigaam_transcriber.transcriber import _ProviderSession, _TranscriptionLimiter


class AsyncSpyProvider(ASRProviderBase):
    """Real async provider recording loop ids, concurrency and close events."""

    def __init__(self, delay: float = 0.0, fail_on: str | None = None,
                 close_exc: Exception | None = None, text: str = "spy-text"):
        self.delay = delay
        self.fail_on = fail_on
        self.close_exc = close_exc
        self.text = text
        self.calls: list[tuple] = []
        self.loop_ids: set[int] = set()
        self.in_flight = 0
        self.peak_in_flight = 0
        self.close_count = 0
        self.closed_on: int | None = None
        self._lock = threading.Lock()

    def _enter(self, name: str, *args) -> None:
        loop_id = id(asyncio.get_running_loop())
        with self._lock:
            self.calls.append((name, loop_id, *args))
            self.loop_ids.add(loop_id)
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

    def _exit(self) -> None:
        with self._lock:
            self.in_flight -= 1

    async def transcribe(self, audio_path, language=None, diarization=True, denoise=False) -> str:
        self._enter("transcribe", audio_path)
        try:
            if self.fail_on and self.fail_on in str(audio_path):
                raise asyncio.CancelledError()
            if self.delay:
                await asyncio.sleep(self.delay)
            return f"{self.text}-{Path(audio_path).stem}" if self.text else ""
        finally:
            self._exit()

    async def transcribe_raw(self, audio_bytes, filename, language=None) -> str:
        self._enter("transcribe_raw", filename)
        try:
            return self.text
        finally:
            self._exit()

    async def transcribe_segments(self, audio_path, segments, language=None):
        self._enter("transcribe_segments", audio_path)
        try:
            return []
        finally:
            self._exit()

    async def close(self) -> None:
        self.close_count += 1
        self.closed_on = id(asyncio.get_running_loop())
        if self.close_exc is not None:
            raise self.close_exc


def _make_transcriber(tmp_path: Path) -> GigaAMTranscriber:
    transcriber = GigaAMTranscriber(api_key="test-key")
    processor = MagicMock()
    processor.is_supported_file.return_value = True
    processor.is_video_file.return_value = False
    processor.get_media_info.return_value = {"sample_rate": 16000, "channels": 1}
    processor.get_duration.return_value = 5.0
    transcriber._audio_processor = processor
    return transcriber


def _make_chunks(tmp_path: Path, count: int) -> list[tuple[Path, float, float]]:
    chunks = []
    for i in range(count):
        path = tmp_path / f"chunk_{i}.wav"
        path.write_bytes(f"chunk-{i}".encode())
        chunks.append((path, float(i * 300), float((i + 1) * 300)))
    return chunks


@pytest.fixture
def limiter_reset(monkeypatch):
    limiter = _TranscriptionLimiter(2)
    monkeypatch.setattr(transcriber_module, "_TRANSCRIPTION_LIMITER", limiter)
    return limiter


class TestSingleLoopOwnership:
    def test_chunked_async_operation_uses_one_loop_and_closes_on_it(self, tmp_path, monkeypatch):
        spy = AsyncSpyProvider()
        transcriber = _make_transcriber(tmp_path)
        transcriber.chunk_threshold = 10.0
        transcriber._audio_processor.get_duration.return_value = 100.0
        (tmp_path / "long.wav").write_bytes(b"audio")
        transcriber._audio_processor.split_audio.return_value = _make_chunks(tmp_path, 5)

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            result = transcriber.transcribe(tmp_path / "long.wav", diarization="none")

        assert len(spy.calls) == 5
        assert len(spy.loop_ids) == 1, "all provider calls must share one event loop"
        assert spy.closed_on in spy.loop_ids, "provider must be closed on its owning loop"
        assert spy.close_count == 1
        assert result.text == " ".join(f"spy-text-chunk_{i}" for i in range(5))
        assert all(not path.exists() for path, _, _ in
                   transcriber._audio_processor.split_audio.return_value)

    def test_chunk_order_preserved_with_reversed_latencies(self, tmp_path, monkeypatch):
        spy = AsyncSpyProvider()
        original = spy.transcribe

        async def reversed_delay(audio_path, **kwargs):
            index = int(Path(audio_path).stem.split("_")[1])
            await asyncio.sleep((5 - index) * 0.02)
            return await original(audio_path, **kwargs)

        monkeypatch.setattr(spy, "transcribe", reversed_delay)
        transcriber = _make_transcriber(tmp_path)
        transcriber.chunk_threshold = 10.0
        transcriber._audio_processor.get_duration.return_value = 100.0
        (tmp_path / "long.wav").write_bytes(b"audio")
        transcriber._audio_processor.split_audio.return_value = _make_chunks(tmp_path, 5)

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            result = transcriber.transcribe(tmp_path / "long.wav", diarization="none")

        assert result.text == " ".join(f"spy-text-chunk_{i}" for i in range(5))

    def test_litellm_operation_creates_exactly_one_client(self, tmp_path, monkeypatch):
        created: list[httpx.AsyncClient] = []
        loops_seen: set[int] = set()
        state = {"in_flight": 0, "peak": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            import re

            body = await request.aread()
            index = int(re.search(rb"chunk-(\d+)", body).group(1))
            loops_seen.add(id(asyncio.get_running_loop()))
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            await asyncio.sleep((5 - index) * 0.01)
            state["in_flight"] -= 1
            return httpx.Response(200, json={"text": f"text-{index}"})

        real_async_client = httpx.AsyncClient

        def factory(**kwargs):
            client = real_async_client(transport=httpx.MockTransport(handler), timeout=10.0)
            created.append(client)
            return client

        monkeypatch.setattr(litellm_module.httpx, "AsyncClient", factory)
        monkeypatch.setattr(transcriber_module, "ASR_CHUNK_CONCURRENCY", 2)
        provider = LiteLLMASRClient(max_retries=0)

        transcriber = _make_transcriber(tmp_path)
        transcriber.chunk_threshold = 10.0
        transcriber._audio_processor.get_duration.return_value = 100.0
        (tmp_path / "long.wav").write_bytes(b"audio")
        transcriber._audio_processor.split_audio.return_value = _make_chunks(tmp_path, 5)

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=provider):
            result = transcriber.transcribe(tmp_path / "long.wav", diarization="none")

        assert result.text == " ".join(f"text-{i}" for i in range(5))
        assert len(created) == 1, "one operation must own exactly one AsyncClient"
        assert len(loops_seen) == 1, "all HTTP I/O must happen on one event loop"
        assert state["peak"] <= 2, "chunk concurrency must respect the semaphore"
        assert provider._closed
        assert created[0].is_closed

    def test_litellm_survives_sequential_event_loops(self, monkeypatch):
        provider = LiteLLMASRClient()
        clients_used: list[httpx.AsyncClient] = []

        async def fake_post(files, data):
            clients_used.append(provider._current_client())
            return "ok"

        provider._post_transcription = fake_post

        async def use():
            return await provider.transcribe_raw(b"audio", "a.wav")

        assert asyncio.run(use()) == "ok"
        assert asyncio.run(use()) == "ok"

        assert clients_used[0] is not clients_used[1], (
            "a new event loop must get a fresh AsyncClient, never the dead loop's one"
        )
        asyncio.run(provider.close())
        assert provider._closed
        assert clients_used[0].is_closed and clients_used[1].is_closed


class TestGlobalConcurrencyBound:
    def test_concurrent_operations_respect_global_limit(self, tmp_path, limiter_reset):
        spy = AsyncSpyProvider(delay=0.1)
        transcriber = _make_transcriber(tmp_path)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"audio")

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            threads = [
                threading.Thread(target=transcriber.transcribe, args=(audio,),
                                 kwargs={"diarization": "none"})
                for _ in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        active, peak = limiter_reset.stats()
        assert (active, peak) == (0, 2)
        assert spy.peak_in_flight == 2
        assert spy.close_count == 6

    def test_chunk_semaphore_bounds_provider_concurrency(self, tmp_path, monkeypatch):
        spy = AsyncSpyProvider(delay=0.05)
        monkeypatch.setattr(transcriber_module, "ASR_CHUNK_CONCURRENCY", 2)
        transcriber = _make_transcriber(tmp_path)
        transcriber.chunk_threshold = 10.0
        transcriber._audio_processor.get_duration.return_value = 100.0
        (tmp_path / "long.wav").write_bytes(b"audio")
        transcriber._audio_processor.split_audio.return_value = _make_chunks(tmp_path, 6)

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            transcriber.transcribe(tmp_path / "long.wav", diarization="none")

        assert spy.peak_in_flight <= 2


class TestCancellationAndCleanup:
    def test_task_cancellation_leaks_nothing(self, tmp_path, limiter_reset):
        spy = AsyncSpyProvider(delay=0.15)
        transcriber = _make_transcriber(tmp_path)
        transcriber.chunk_threshold = 10.0
        transcriber._audio_processor.get_duration.return_value = 100.0
        chunks = _make_chunks(tmp_path, 5)
        transcriber._audio_processor.split_audio.return_value = chunks
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"audio")
        worker_done = threading.Event()

        def work():
            try:
                return transcriber.transcribe(audio, diarization="none")
            finally:
                worker_done.set()

        async def scenario():
            loop = asyncio.get_running_loop()
            task = asyncio.create_task(_consume(loop.run_in_executor(None, work)))
            await asyncio.sleep(0.08)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        async def _consume(future):
            return await future

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            asyncio.run(scenario())

        assert worker_done.wait(timeout=30), "worker thread must drain after cancellation"

        assert all(not path.exists() for path, _, _ in chunks), "every temp chunk must be deleted"
        assert spy.close_count == 1, "operation-owned provider must be closed exactly once"
        assert spy.closed_on in spy.loop_ids
        active, _ = limiter_reset.stats()
        assert active == 0

    def test_session_cancel_stops_pending_chunks(self, tmp_path):
        spy = AsyncSpyProvider(delay=0.05, fail_on="chunk_2")

        with _ProviderSession(spy) as session:
            with pytest.raises(asyncio.CancelledError):
                session.gather(
                    "transcribe",
                    [(str(path),) for path, _, _ in _make_chunks(tmp_path, 5)],
                    limit=1,
                )

        started = {Path(call[2]).stem for call in spy.calls}
        assert started == {"chunk_0", "chunk_1", "chunk_2"}, (
            "chunks queued behind the failure must never start"
        )
        assert spy.close_count == 1

    def test_provider_close_failure_does_not_mask_original_error(self, tmp_path):
        spy = AsyncSpyProvider(text="", close_exc=RuntimeError("close boom"))
        transcriber = _make_transcriber(tmp_path)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"audio")

        with patch("gigaam_transcriber.transcriber.get_asr_provider", return_value=spy):
            with pytest.raises(EmptyAudioError):
                transcriber.transcribe(audio, diarization="none")

        assert spy.close_count == 1


class TestExactlyOnceClose:
    def test_litellm_close_is_idempotent(self):
        provider = LiteLLMASRClient()
        asyncio.run(provider.close())
        asyncio.run(provider.close())
        assert provider._closed

    def test_litellm_rejects_work_after_close(self, tmp_path):
        provider = LiteLLMASRClient()
        asyncio.run(provider.close())
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"audio")
        with pytest.raises(ASRError, match="closed"):
            asyncio.run(provider.transcribe(str(audio)))

    def test_mistral_close_is_idempotent_and_guards(self, tmp_path):
        import numpy as np
        import soundfile as sf

        provider = MistralASRClient(api_key="k")
        provider.close()
        provider.close()
        audio = tmp_path / "a.wav"
        sf.write(audio, np.zeros(16000, dtype="float32"), 16000)
        with pytest.raises(ASRError, match="closed"):
            provider.transcribe(str(audio))

    def test_session_close_twice_calls_provider_once(self):
        spy = AsyncSpyProvider()
        session = _ProviderSession(spy)
        session.close()
        session.close()
        assert spy.close_count == 1

    def test_transcriber_cleanup_idempotent_and_lazy_reload(self, tmp_path, monkeypatch):
        transcriber = _make_transcriber(tmp_path)
        transcriber._diarization_manager = MagicMock()

        with patch("gigaam_transcriber.transcriber.AudioProcessor") as mock_ap:
            transcriber.cleanup()
            transcriber.cleanup()

            assert transcriber._diarization_manager is None
            assert transcriber._audio_processor is None
            _ = transcriber.audio_processor
            mock_ap.assert_called_once()

    def test_session_without_calls_still_closes_provider(self):
        spy = AsyncSpyProvider()
        with _ProviderSession(spy):
            pass
        assert spy.close_count == 1
        assert spy.calls == []
