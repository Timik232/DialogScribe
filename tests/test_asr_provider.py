"""
Тесты ASR-провайдеров: контракт transcribe_segments, fallback, фабрика.
"""

import asyncio

import numpy as np
import pytest
import soundfile as sf
from unittest.mock import AsyncMock, MagicMock

from gigaam_transcriber.asr_provider import FallbackASRProvider, get_asr_provider
from gigaam_transcriber.exceptions import ASRError
from gigaam_transcriber.litellm_client import LiteLLMASRClient
from gigaam_transcriber.mistral_client import MistralASRClient


def _make_mistral_client(text: str = "текст") -> MistralASRClient:
    client = MistralASRClient(min_request_interval=0)
    audio = np.zeros(32000, dtype="float32")
    client._load_audio = MagicMock(return_value=(audio, 16000))
    client._send_transcription_request = MagicMock(return_value=text)
    return client


def test_mistral_transcribe_segments_accepts_language(tmp_path):
    """Контракт ASRProviderBase: transcribe_segments принимает language."""
    client = _make_mistral_client()
    segments = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]

    result = client.transcribe_segments(str(tmp_path / "a.wav"), segments, language="ru")

    assert result[0].text == "текст"
    assert result[0].speaker == "SPEAKER_00"


def test_fallback_to_mistral_secondary_no_typeerror(tmp_path):
    """GigaAM (litellm) падает → mistral-секондари не должен ломаться с TypeError."""
    audio_path = tmp_path / "audio.wav"
    sf.write(audio_path, np.zeros(16000, dtype="float32"), 16000)

    litellm = LiteLLMASRClient()

    async def failing_post(files, data):
        raise ASRError("litellm down")

    litellm._post_transcription = failing_post

    mistral = _make_mistral_client("fallback text")
    provider = FallbackASRProvider(
        primary=litellm,
        secondary=mistral,
        primary_name="litellm",
        secondary_name="mistral",
    )

    result = asyncio.run(
        provider.transcribe_segments(
            str(audio_path), [{"start": 0.0, "end": 1.0, "speaker": "SPK"}], language="ru",
        )
    )

    assert result[0].text == "fallback text"


def test_unknown_provider_defaults_to_litellm():
    """Неизвестный provider (напр. легаси 'gigaam') маршрутизируется на litellm."""
    provider = get_asr_provider("gigaam")
    try:
        assert provider._primary_name == "litellm"
    finally:
        asyncio.run(provider.close())


def test_litellm_transcribe_segments_slices_audio(tmp_path):
    """Каждый сегмент отправляется отдельным куском аудио, а не весь файл."""
    audio_path = tmp_path / "audio.wav"
    sf.write(audio_path, np.zeros(16000, dtype="float32"), 16000)

    client = LiteLLMASRClient()
    posted_sizes: list[int] = []

    async def fake_post(files, data):
        posted_sizes.append(len(files["file"][1]))
        return "seg"

    client._post_transcription = fake_post

    segments = [
        {"start": 0.0, "end": 0.5, "speaker": "A"},
        {"start": 0.0, "end": 1.0, "speaker": "B"},
    ]
    result = asyncio.run(client.transcribe_segments(str(audio_path), segments, language="ru"))

    assert [seg.text for seg in result] == ["seg", "seg"]
    assert len(posted_sizes) == 2
    assert posted_sizes[0] < posted_sizes[1]
