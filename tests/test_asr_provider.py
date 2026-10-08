"""
Тесты ASR-провайдеров: контракт transcribe_segments, fallback, фабрика.
"""

import asyncio

import numpy as np
import pytest
import soundfile as sf
from unittest.mock import AsyncMock, MagicMock

from gigaam_transcriber.asr_provider import (
    ASRProviderBase,
    FallbackASRProvider,
    get_asr_provider,
)
from gigaam_transcriber.data_models import TranscriptionSegment
from gigaam_transcriber.exceptions import ASRError
from gigaam_transcriber.litellm_client import LiteLLMASRClient
from gigaam_transcriber.mistral_client import MistralASRClient


class _RecordingProvider(ASRProviderBase):
    """Реальный async-провайдер, записывающий все вызовы (спай)."""

    def __init__(self, result: str = "ok", exc: Exception | None = None):
        self.calls: list[tuple] = []
        self._result = result
        self._exc = exc

    async def transcribe(self, audio_path, language=None, diarization=True, denoise=False) -> str:
        self.calls.append(("transcribe", audio_path, language, diarization, denoise))
        if self._exc is not None:
            raise self._exc
        return self._result

    async def transcribe_raw(self, audio_bytes, filename, language=None) -> str:
        self.calls.append(("transcribe_raw", filename, language))
        if self._exc is not None:
            raise self._exc
        return self._result

    async def transcribe_segments(self, audio_path, segments, language=None) -> list[TranscriptionSegment]:
        self.calls.append(("transcribe_segments", audio_path, language))
        if self._exc is not None:
            raise self._exc
        return [
            TranscriptionSegment(text=self._result, start=s["start"], end=s["end"], speaker=s.get("speaker"))
            for s in segments
        ]

    async def close(self) -> None:
        self.calls.append(("close",))


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


def test_fallback_transcribe_forwards_all_options_to_both_providers(tmp_path):
    """CQ-H3: language/diarization/denoise доезжают и до primary, и до fallback."""
    audio = str(tmp_path / "a.wav")
    failing = _RecordingProvider(exc=ASRError("primary down"))
    secondary = _RecordingProvider(result="fallback text")
    provider = FallbackASRProvider(
        primary=failing, secondary=secondary,
        primary_name="mistral", secondary_name="litellm",
    )

    result = asyncio.run(
        provider.transcribe(audio, language="en", diarization=False, denoise=True)
    )

    assert result == "fallback text"
    expected = ("transcribe", audio, "en", False, True)
    assert failing.calls[0] == expected
    assert secondary.calls[0] == expected


def test_fallback_transcribe_segments_forwards_language_to_secondary(tmp_path):
    """CQ-H3: при падении primary сегментный путь передает language в fallback."""
    audio = str(tmp_path / "a.wav")
    failing = _RecordingProvider(exc=ASRError("primary down"))
    secondary = _RecordingProvider(result="seg-text")
    provider = FallbackASRProvider(
        primary=failing, secondary=secondary,
        primary_name="mistral", secondary_name="litellm",
    )
    segments = [{"start": 0.0, "end": 1.0, "speaker": "SPK"}]

    result = asyncio.run(provider.transcribe_segments(audio, segments, language="de"))

    assert result[0].text == "seg-text"
    assert failing.calls[0] == ("transcribe_segments", audio, "de")
    assert secondary.calls[0] == ("transcribe_segments", audio, "de")


def test_fallback_transcribe_raw_forwards_language_to_both(tmp_path):
    """CQ-H3: transcribe_raw передает language обоим провайдерам."""
    failing = _RecordingProvider(exc=ASRError("primary down"))
    secondary = _RecordingProvider(result="raw text")
    provider = FallbackASRProvider(
        primary=failing, secondary=secondary,
        primary_name="mistral", secondary_name="litellm",
    )

    result = asyncio.run(provider.transcribe_raw(b"bytes", "file.webm", language="fr"))

    assert result == "raw text"
    assert failing.calls[0] == ("transcribe_raw", "file.webm", "fr")
    assert secondary.calls[0] == ("transcribe_raw", "file.webm", "fr")


def test_mistral_client_transcribe_accepts_contract_kwargs(tmp_path):
    """MistralASRClient.transcribe принимает language/diarization/denoise без TypeError."""
    client = _make_mistral_client("contract ok")
    audio_path = str(tmp_path / "a.wav")

    result = client.transcribe(audio_path, language="ru", diarization=False, denoise=True)

    assert result == "contract ok"


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
