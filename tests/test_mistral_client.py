"""
Тесты для модуля mistral_client (клиент Mistral API).

Тестирует механизм ограничения частоты запросов (rate limiting).
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.mistral_client import MistralASRClient
from gigaam_transcriber.transcriber import GigaAMTranscriber


def _success_response(text: str = "hello") -> MagicMock:
    """Создать успешный mock-ответ Mistral API."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"text": text}
    return response


def test_first_request_no_delay():
    """Первый запрос отправляется без задержки rate limiting."""
    client = MistralASRClient(min_request_interval=1.0)
    client._client.post = MagicMock(return_value=_success_response("hello"))

    with patch("gigaam_transcriber.mistral_client.time.sleep") as mock_sleep:
        text = client._send_transcription_request(b"fake_wav_bytes")

    assert text == "hello"
    mock_sleep.assert_not_called()


def test_second_request_within_interval_sleeps():
    """Второй запрос внутри интервала вызывает sleep на оставшееся время."""
    client = MistralASRClient(min_request_interval=1.0)
    client._client.post = MagicMock(return_value=_success_response("hello"))

    with (
        patch("gigaam_transcriber.mistral_client.time.monotonic") as mock_monotonic,
        patch("gigaam_transcriber.mistral_client.time.sleep") as mock_sleep,
    ):
        mock_monotonic.side_effect = [100.0, 100.0, 100.1, 100.1]

        client._send_transcription_request(b"fake_wav_bytes")
        client._send_transcription_request(b"fake_wav_bytes")

    assert mock_sleep.call_count == 1
    assert mock_sleep.call_args[0][0] == pytest.approx(0.9, abs=0.01)


def test_request_after_interval_gap_no_sleep():
    """Если между запросами прошёл интервал, дополнительный sleep не нужен."""
    client = MistralASRClient(min_request_interval=1.0)
    client._client.post = MagicMock(return_value=_success_response("hello"))

    with (
        patch("gigaam_transcriber.mistral_client.time.monotonic") as mock_monotonic,
        patch("gigaam_transcriber.mistral_client.time.sleep") as mock_sleep,
    ):
        mock_monotonic.side_effect = [100.0, 100.0, 102.0, 102.0]

        client._send_transcription_request(b"fake_wav_bytes")
        client._send_transcription_request(b"fake_wav_bytes")

    mock_sleep.assert_not_called()


def test_zero_interval_disables_rate_limiting():
    """Нулевой интервал полностью отключает проактивный rate limiting."""
    client = MistralASRClient(min_request_interval=0)
    client._client.post = MagicMock(return_value=_success_response("hello"))

    with patch("gigaam_transcriber.mistral_client.time.sleep") as mock_sleep:
        client._send_transcription_request(b"fake_wav_bytes")
        client._send_transcription_request(b"fake_wav_bytes")

    mock_sleep.assert_not_called()


def test_concurrent_requests_respect_interval():
    """Параллельные запросы соблюдают минимальный интервал между POST вызовами."""
    client = MistralASRClient(min_request_interval=1.0)
    post_timestamps: list[float] = []
    timestamps_lock = threading.Lock()

    def post_side_effect(*args, **kwargs):
        with timestamps_lock:
            post_timestamps.append(time.monotonic())
        return _success_response("hello")

    client._client.post = MagicMock(side_effect=post_side_effect)

    def worker():
        return client._send_transcription_request(b"fake_wav_bytes")

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: worker(), range(3)))

    assert results == ["hello", "hello", "hello"]
    assert len(post_timestamps) == 3

    sorted_timestamps = sorted(post_timestamps)
    gaps = [
        sorted_timestamps[idx + 1] - sorted_timestamps[idx]
        for idx in range(len(sorted_timestamps) - 1)
    ]

    assert all(gap >= 0.9 for gap in gaps)


def test_transcriber_forwards_interval():
    """Транскрайбер корректно пробрасывает min_request_interval в ASR-клиент."""
    with patch("gigaam_transcriber.mistral_client.httpx.Client"):
        transcriber = GigaAMTranscriber(min_request_interval=2.0)
        client = transcriber.asr_client

    assert client._min_request_interval == 2.0
    transcriber.cleanup()


def test_long_request_no_extra_delay():
    """Долгий запрос сам формирует паузу, поэтому второй запрос без sleep."""
    client = MistralASRClient(min_request_interval=1.0)
    client._client.post = MagicMock(return_value=_success_response("hello"))

    with (
        patch("gigaam_transcriber.mistral_client.time.monotonic") as mock_monotonic,
        patch("gigaam_transcriber.mistral_client.time.sleep") as mock_sleep,
    ):
        mock_monotonic.side_effect = [100.0, 100.0, 105.0, 105.0]

        client._send_transcription_request(b"fake_wav_bytes")
        client._send_transcription_request(b"fake_wav_bytes")

    mock_sleep.assert_not_called()
