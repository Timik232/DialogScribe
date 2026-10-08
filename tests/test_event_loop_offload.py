"""Task 14 tests: event-loop offload of blocking LLM analysis stages.

Heartbeat tests run the real compute stages (summary / chat / insights /
mindmap / meeting-prep) on an event loop with an intentionally slow (2s)
blocking LLM and assert loop latency stays far below the block duration.
The saturation test proves asyncio.to_thread queues work beyond the default
executor's worker limit without stalling the loop.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gigaam_transcriber import chat as chat_module
from gigaam_transcriber.insights import extract_action_items
from gigaam_transcriber.meeting_prep.service import generate_meeting_prep
from gigaam_transcriber.mindmap import generate_mindmap_markdown
from gigaam_transcriber.summarizer import generate_summary

BLOCK_SECONDS = 2.0
HEARTBEAT_INTERVAL = 0.05
# Loop must stay at least an order of magnitude faster than the injected
# block; a blocked loop would show >= BLOCK_SECONDS spikes.
HEARTBEAT_MAX_LATENCY = 0.5


class RecordingSlowLLM:
    """Real (non-Mock) synchronous LLM client whose calls block for `delay`."""

    def __init__(self, answer="# Тема\n## Ключевые моменты\n- факт", delay=BLOCK_SECONDS):
        self.delay = delay
        self.answer = answer
        self.config = SimpleNamespace(model="gpt-4.1", api_key="k", base_url="http://test")
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.call_threads: list[threading.Thread] = []

    def call(self, system_prompt, user_text, max_tokens=4096, model_override=None):
        with self._lock:
            self.calls.append(
                {"system": system_prompt, "user": user_text, "model": model_override}
            )
            self.call_threads.append(threading.current_thread())
        time.sleep(self.delay)
        return self.answer

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)


def _slow_chat_client(delay: float = BLOCK_SECONDS) -> MagicMock:
    """OpenAI-surface mock for chat_with_transcript with a blocking create()."""
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Answer"

    def slow_create(**kwargs):
        time.sleep(delay)
        return response

    client._get_client.return_value.chat.completions.create.side_effect = slow_create
    client.config.model = "gpt-4.1"
    client.config.api_key = "key"
    client.config.base_url = "http://test"
    return client


async def _run_with_heartbeat(workload):
    """Run an async workload while a heartbeat task measures loop latency."""
    samples: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            t0 = time.monotonic()
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            samples.append(time.monotonic() - t0)

    hb = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        result = await workload()
    finally:
        stop.set()
        await hb
    return result, samples, time.monotonic() - started


def _assert_heartbeat_held(samples: list[float], elapsed: float) -> None:
    assert samples, "heartbeat produced no samples"
    worst = max(samples)
    assert worst < HEARTBEAT_MAX_LATENCY, (
        f"event loop stalled: worst heartbeat latency {worst:.3f}s "
        f">= {HEARTBEAT_MAX_LATENCY}s while a {BLOCK_SECONDS}s blocking LLM ran"
    )
    assert elapsed >= BLOCK_SECONDS, "workload did not actually block"


# ---------------------------------------------------------------------------
# 1. Heartbeat: slow (2s) blocking LLM in every analysis path
# ---------------------------------------------------------------------------


class TestHeartbeatUnderSlowLLM:
    @pytest.mark.asyncio
    async def test_summary_heartbeat(self):
        llm = RecordingSlowLLM()
        result, samples, elapsed = await _run_with_heartbeat(
            lambda: generate_summary("Обсуждение проекта.", "general", llm)
        )
        assert result == llm.answer
        assert llm.call_count == 1
        _assert_heartbeat_held(samples, elapsed)
        assert llm.call_threads[0] is not threading.main_thread()

    @pytest.mark.asyncio
    async def test_chat_heartbeat(self):
        client = _slow_chat_client()
        result, samples, elapsed = await _run_with_heartbeat(
            lambda: asyncio.to_thread(
                chat_module.chat_with_transcript,
                text="Speaker 1: короткий транскрипт.",
                messages=[{"role": "user", "content": "О чём речь?"}],
                llm_client=client,
            )
        )
        assert result == {"answer": "Answer"}
        _assert_heartbeat_held(samples, elapsed)

    @pytest.mark.asyncio
    async def test_insights_heartbeat(self):
        llm = RecordingSlowLLM(
            answer=(
                '{"action_items": [{"task": "подготовить отчёт"}], '
                '"decisions": [{"decision": "принять план"}]}'
            )
        )
        result, samples, elapsed = await _run_with_heartbeat(
            lambda: asyncio.to_thread(extract_action_items, "Текст встречи.", llm)
        )
        assert result["action_items"][0]["task"] == "подготовить отчёт"
        assert llm.call_count == 1
        _assert_heartbeat_held(samples, elapsed)

    @pytest.mark.asyncio
    async def test_mindmap_heartbeat(self):
        llm = RecordingSlowLLM()
        result, samples, elapsed = await _run_with_heartbeat(
            lambda: asyncio.to_thread(generate_mindmap_markdown, "Текст встречи.", llm)
        )
        assert isinstance(result, str) and result
        assert llm.call_count == 1
        _assert_heartbeat_held(samples, elapsed)

    @pytest.mark.asyncio
    async def test_meeting_prep_heartbeat(self):
        llm = RecordingSlowLLM()
        result, samples, elapsed = await _run_with_heartbeat(
            lambda: generate_meeting_prep("Компания ООО Ромашка.", "Каталог: CRM.", llm)
        )
        assert result == (llm.answer, "gpt-4.1")
        assert llm.call_count == 1
        # Single-shot strategy must include BOTH data sources in the prompt.
        assert "Ромашка" in llm.calls[0]["user"]
        assert "CRM" in llm.calls[0]["user"]
        _assert_heartbeat_held(samples, elapsed)

    @pytest.mark.asyncio
    async def test_saved_transcriptions_analysis_pipeline_heartbeat(self):
        """The full 4-stage analyze pipeline (summary/mindmap/insights/chat)."""
        llm = RecordingSlowLLM(delay=0.5)

        async def pipeline():
            text = "Текст сохранённой транскрипции."
            summary = await generate_summary(text, "general", llm)
            mindmap = await asyncio.to_thread(generate_mindmap_markdown, text, llm)
            insights = await asyncio.to_thread(extract_action_items, text, llm)
            chat = await asyncio.to_thread(
                chat_module.chat_with_transcript,
                text=text,
                messages=[{"role": "user", "content": "q"}],
                llm_client=_slow_chat_client(delay=0.5),
            )
            return summary, mindmap, insights, chat

        result, samples, _ = await _run_with_heartbeat(pipeline)
        assert result[0] == llm.answer
        assert result[1]
        assert result[3] == {"answer": "Answer"}
        assert llm.call_count == 3
        assert samples and max(samples) < HEARTBEAT_MAX_LATENCY


# ---------------------------------------------------------------------------
# 2. Executor saturation: to_thread queues beyond the executor limit
# ---------------------------------------------------------------------------


class TestExecutorSaturation:
    @pytest.mark.asyncio
    async def test_saturation_above_executor_limit(self):
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=2)
        loop.set_default_executor(executor)

        def block(idx: int) -> int:
            time.sleep(0.2)
            return idx

        samples: list[float] = []
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                t0 = time.monotonic()
                await asyncio.sleep(0.02)
                samples.append(time.monotonic() - t0)

        try:
            hb = asyncio.create_task(heartbeat())
            tasks = [asyncio.create_task(asyncio.to_thread(block, i)) for i in range(8)]
            started = time.monotonic()
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            await hb

        assert sorted(results) == list(range(8))
        # 8 x 0.2s jobs over 2 workers must serialize: >= 4 waves.
        assert elapsed >= 0.75, f"jobs did not queue above the executor limit ({elapsed:.2f}s)"
        assert samples and max(samples) < HEARTBEAT_MAX_LATENCY, (
            "loop stalled while default executor was saturated"
        )
