"""
Тесты исправлений надёжности диаризации:

1. Ретраи 502/504 в LiteLLM-клиенте (транзиентные сбои triton-адаптера
   под нагрузкой батчей сегментов раньше убивали весь прогон).
2. Адаптивный порог тишины в split_by_silence (фиксированный −40 дБ
   не находил пауз на шумных записях → гибридная диаризация
   деградировала в одного спикера на весь файл).
"""

import asyncio

import pytest

from gigaam_transcriber.asr_provider import get_asr_provider
from gigaam_transcriber.exceptions import ASRError
from gigaam_transcriber.litellm_client import LiteLLMASRClient


class TestLiteLLMRetries:
    def _status_response(self, status: int):
        import httpx

        request = httpx.Request("POST", "http://x/v1/audio/transcriptions")
        return httpx.Response(status, request=request, text="err")

    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    def test_transient_statuses_are_retried(self, status):
        """429/502/503/504 ретраятся, а не падают сразу."""
        client = LiteLLMASRClient(max_retries=2)
        attempts = []

        async def fake_post(url, **kwargs):
            attempts.append(1)
            response = self._status_response(status)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response

        client._client = type("C", (), {"post": staticmethod(fake_post), "aclose": staticmethod(lambda: asyncio.sleep(0))})()

        with pytest.raises(ASRError):
            asyncio.run(client.transcribe_raw(b"audio", "a.wav"))

        # 1 первичная + 2 ретрая = 3 попытки
        assert len(attempts) == 3

    def test_502_recovers_after_retry(self):
        """Один транзиентный 502, затем успех — запрос завершается нормально."""
        client = LiteLLMASRClient(max_retries=2)
        attempts = []

        async def fake_post(url, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                response = self._status_response(502)
                response.raise_for_status()
                return response
            import json as _json

            import httpx

            request = httpx.Request("POST", "http://x/v1/audio/transcriptions")
            return httpx.Response(
                200, request=request, text=_json.dumps({"text": "текст"}),
            )

        client._client = type("C", (), {"post": staticmethod(fake_post), "aclose": staticmethod(lambda: asyncio.sleep(0))})()

        result = asyncio.run(client.transcribe_raw(b"audio", "a.wav"))
        assert result == "текст"
        assert len(attempts) == 2

    def test_400_not_retried(self):
        """Клиентские ошибки (4xx кроме 429) не ретраятся."""
        client = LiteLLMASRClient(max_retries=3)
        attempts = []

        async def fake_post(url, **kwargs):
            attempts.append(1)
            response = self._status_response(400)
            response.raise_for_status()
            return response

        client._client = type("C", (), {"post": staticmethod(fake_post), "aclose": staticmethod(lambda: asyncio.sleep(0))})()

        with pytest.raises(ASRError):
            asyncio.run(client.transcribe_raw(b"audio", "a.wav"))

        assert len(attempts) == 1


class TestAdaptiveSilenceThreshold:
    def _audio_processor(self):
        from gigaam_transcriber.audio_processor import AudioProcessor

        return AudioProcessor()

    def test_adaptive_threshold_derived_from_dbfs(self, tmp_path, monkeypatch):
        """Порог тишины вычисляется от dBFS записи, а не фиксирован в −40."""
        import numpy as np
        import soundfile as sf

        proc = self._audio_processor()
        # Синтетика: громкая «речь» (тон −12 dBFS) с паузами
        sr = 16000
        duration = 6
        t = np.linspace(0, duration, sr * duration, endpoint=False)
        audio = np.zeros(sr * duration, dtype="float32")
        speech = 0.25 * np.sin(2 * np.pi * 300 * t)  # ~ −12 dBFS
        audio[0 : sr * 2] = speech[0 : sr * 2]  # речь 0-2с
        audio[sr * 3 : sr * 6] = speech[sr * 3 : sr * 6]  # речь 3-6с
        path = tmp_path / "a.wav"
        sf.write(path, audio, sr)

        seen_thresholds = []
        import pydub.silence

        orig = pydub.silence.detect_nonsilent

        def spy(audio_seg, **kwargs):
            seen_thresholds.append(kwargs.get("silence_thresh"))
            return orig(audio_seg, **kwargs)

        monkeypatch.setattr(pydub.silence, "detect_nonsilent", spy)

        segments = proc.split_by_silence(path)

        # Порог адаптирован под уровень записи (dBFS ≈ −9…−15, порог ≈ dBFS−16)
        assert seen_thresholds, "detect_nonsilent не вызван"
        assert seen_thresholds[0] > -40
        # Пауза 2-3с найдена: минимум 2 сегмента
        assert len(segments) >= 2

    def test_long_segment_capped(self, tmp_path):
        """Непрерывный участок без пауз режется на куски ≤ max_segment_seconds."""
        import numpy as np
        import soundfile as sf

        proc = self._audio_processor()
        sr = 16000
        t = np.linspace(0, 60, sr * 60, endpoint=False)
        audio = (0.25 * np.sin(2 * np.pi * 300 * t)).astype("float32")
        path = tmp_path / "a.wav"
        sf.write(path, audio, sr)

        segments = proc.split_by_silence(path, max_segment_seconds=15.0)

        assert len(segments) >= 3
        assert all((end - start) <= 15.0 + 0.01 for start, end in segments)

    def test_explicit_threshold_still_honored(self, tmp_path):
        """Явный silence_thresh сохраняет старое поведение (обратная совместимость)."""
        import numpy as np
        import soundfile as sf

        proc = self._audio_processor()
        sr = 16000
        t = np.linspace(0, 4, sr * 4, endpoint=False)
        audio = (0.25 * np.sin(2 * np.pi * 300 * t)).astype("float32")
        path = tmp_path / "a.wav"
        sf.write(path, audio, sr)

        segments = proc.split_by_silence(path, silence_thresh=-40, max_segment_seconds=0)

        # С явным −40 непрерывная речь — один сегмент, без нарезки
        assert len(segments) == 1


class TestLegacyMistralRemoval:
    def test_factory_has_no_mistral_enum(self):
        """Enum ASRProvider больше не содержит mistral."""
        from gigaam_transcriber.asr_provider import ASRProvider

        assert not hasattr(ASRProvider, "MISTRAL")

    def test_factory_mistral_maps_to_litellm(self):
        provider = get_asr_provider("mistral")
        try:
            assert isinstance(provider, LiteLLMASRClient)
        finally:
            result = provider.close()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
