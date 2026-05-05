"""
Клиент облачного ASR сервиса Mistral Voxtral.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import io
import logging
import time
import threading
from typing import Any, Optional

import httpx

from .data_models import TranscriptionSegment
from .exceptions import AudioProcessingError, ASRError

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


@dataclass
class _SegmentSpec:
    """Внутреннее описание сегмента для батч-транскрипции."""

    start: float
    end: float
    speaker: Optional[str] = None


class MistralASRClient:
    """Клиент для Mistral API `/v1/audio/transcriptions`.

    Args:
        min_request_interval: Минимальный интервал между запросами к API (секунды).
            0 = без ограничений.
    """

    def __init__(
        self,
        asr_url: str = "https://api.mistral.ai",
        model: str = "voxtral-mini-latest",
        api_key: str = "",
        proxy: Optional[str] = None,
        min_request_interval: float = 1.0,
    ) -> None:
        self.asr_url = asr_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        client_kwargs: dict[str, Any] = {"timeout": 300.0}
        if proxy:
            client_kwargs["proxy"] = proxy
        self._client = httpx.Client(**client_kwargs)
        self._min_request_interval = min_request_interval
        self._last_request_time: float = 0.0  # time.monotonic() timestamp
        self._rate_limit_lock = threading.Lock()

    def close(self) -> None:
        """Закрыть HTTP-клиент и освободить ресурсы."""
        self._client.close()

    def _load_audio(self, audio_path: str) -> tuple[Any, int]:
        """Загрузка аудио, перевод в mono и ресэмплинг до 16kHz."""
        sf = importlib.import_module("soundfile")
        try:
            audio, sr = sf.read(audio_path, always_2d=False)
        except Exception as exc:
            logger.exception("Не удалось прочитать аудио")
            raise AudioProcessingError(
                "Не удалось прочитать аудиофайл",
                file_path=audio_path,
                cause=exc,
            ) from exc

        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)

        if sr != SAMPLE_RATE:
            try:
                import numpy as np

                duration = len(audio) / sr
                target_len = int(duration * SAMPLE_RATE)
                x_old = np.linspace(0, duration, len(audio), endpoint=False)
                x_new = np.linspace(0, duration, target_len, endpoint=False)
                audio = np.interp(x_new, x_old, audio)
                sr = SAMPLE_RATE
            except Exception as exc:
                logger.exception("Не удалось ресэмплировать аудио")
                raise AudioProcessingError(
                    f"Не удалось ресэмплировать аудио до {SAMPLE_RATE} Гц",
                    file_path=audio_path,
                    cause=exc,
                ) from exc

        return audio.astype("float32"), sr

    @staticmethod
    def _encode_wav(audio: Any, sr: int) -> bytes:
        """Кодирование numpy-массива в WAV-байты."""
        sf = importlib.import_module("soundfile")
        buffer = io.BytesIO()
        sf.write(buffer, audio, sr, format="WAV")
        return buffer.getvalue()

    def _send_transcription_request(self, wav_bytes: bytes) -> str:
        """Отправить запрос к Mistral API с retry/backoff."""
        url = f"{self.asr_url}/v1/audio/transcriptions"

        # Проактивное ограничение частоты запросов к API
        if self._min_request_interval > 0:
            with self._rate_limit_lock:
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if elapsed < self._min_request_interval:
                    time.sleep(self._min_request_interval - elapsed)
                self._last_request_time = time.monotonic()

        files = {
            "file": ("audio.wav", wav_bytes, "application/octet-stream"),
        }
        data = {"model": self.model}
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                if attempt == max_retries:
                    logger.exception("Таймаут ASR после %d попыток", max_retries)
                    raise ASRError("таймаут запроса к ASR", cause=exc) from exc
                backoff = min(1.0 * (2.0**attempt), 10.0)
                logger.warning(
                    "ASR timeout (attempt %d/%d), retry in %.1fs",
                    attempt + 1,
                    max_retries,
                    backoff,
                )
                time.sleep(backoff)
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < max_retries:
                    backoff = min(1.0 * (2.0**attempt), 10.0)
                    logger.warning(
                        "ASR status %d (attempt %d/%d), retry in %.1fs",
                        exc.response.status_code,
                        attempt + 1,
                        max_retries,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                logger.exception("ASR вернул HTTP ошибку")
                raise ASRError(
                    f"HTTP {exc.response.status_code}: {exc.response.text}",
                    cause=exc,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == max_retries:
                    logger.exception("ASR request error after %d retries", max_retries)
                    raise ASRError("ошибка HTTP-запроса к ASR", cause=exc) from exc
                backoff = min(1.0 * (2.0**attempt), 10.0)
                logger.warning(
                    "ASR request error (attempt %d/%d), retry in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
                continue

            payload = response.json()
            text = payload.get("text", "")
            if text is None:
                text = ""
            return text

        raise ASRError("не удалось получить ответ от ASR")

    def transcribe(
        self,
        audio_path: str,
        start: float | None = None,
        end: float | None = None,
    ) -> str:
        """Транскрипция всего файла или указанного сегмента."""
        logger.info(
            "ASR transcribe: start=%.2f end=%.2f [path=%s]",
            start if start is not None else 0.0,
            end if end is not None else -1.0,
            audio_path,
        )

        audio, sr = self._load_audio(audio_path)

        if start is not None or end is not None:
            start_index = int(start * SAMPLE_RATE) if start is not None else 0
            end_index = int(end * SAMPLE_RATE) if end is not None else len(audio)
            audio = audio[start_index:end_index]

        if getattr(audio, "size", 0) == 0:
            return ""

        wav_bytes = self._encode_wav(audio, sr)
        text = self._send_transcription_request(wav_bytes)
        logger.info("ASR complete: text_len=%d", len(text))
        return text

    def transcribe_raw(
        self,
        audio_path: str,
        content_type: str = "audio/webm",
    ) -> str:
        """Transcribe audio file directly — no soundfile/ffmpeg processing.

        Sends raw file bytes to Mistral API with the given content type.
        Use for formats Mistral accepts natively (webm, mp3, etc.).

        Args:
            audio_path: Path to the audio file.
            content_type: MIME type for the upload (default: audio/webm).

        Returns:
            Transcribed text (may be empty string).
        """
        logger.info("ASR transcribe_raw: [path=%s, type=%s]", audio_path, content_type)

        with open(audio_path, "rb") as f:
            raw_bytes = f.read()

        if not raw_bytes:
            return ""

        ext = content_type.split("/")[-1]

        url = f"{self.asr_url}/v1/audio/transcriptions"

        if self._min_request_interval > 0:
            with self._rate_limit_lock:
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if elapsed < self._min_request_interval:
                    time.sleep(self._min_request_interval - elapsed)
                self._last_request_time = time.monotonic()

        files = {
            "file": (f"audio.{ext}", raw_bytes, content_type),
        }
        data = {"model": self.model}
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                if attempt == max_retries:
                    logger.exception("ASR raw timeout after %d attempts", max_retries)
                    raise ASRError("таймаут запроса к ASR", cause=exc) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "ASR raw timeout (attempt %d/%d), retry in %.1fs",
                    attempt + 1,
                    max_retries,
                    backoff,
                )
                time.sleep(backoff)
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < max_retries:
                    backoff = min(1.0 * (2.0 ** attempt), 10.0)
                    logger.warning(
                        "ASR raw status %d (attempt %d/%d), retry in %.1fs",
                        exc.response.status_code,
                        attempt + 1,
                        max_retries,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise ASRError(
                    f"HTTP {exc.response.status_code}: {exc.response.text}",
                    cause=exc,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == max_retries:
                    logger.exception("ASR raw request error after %d retries", max_retries)
                    raise ASRError("ошибка HTTP-запроса к ASR", cause=exc) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "ASR raw request error (attempt %d/%d), retry in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
                continue

            payload = response.json()
            text = payload.get("text", "") or ""
            logger.info("ASR transcribe_raw complete: text_len=%d", len(text))
            return text

        raise ASRError("не удалось получить ответ от ASR")

    def _parse_segment(self, segment: Any) -> _SegmentSpec:
        """Нормализация входного описания сегмента."""
        if isinstance(segment, dict):
            return _SegmentSpec(
                start=float(segment.get("start", 0.0)),
                end=float(segment.get("end", 0.0)),
                speaker=segment.get("speaker"),
            )

        if isinstance(segment, (list, tuple)) and len(segment) >= 2:
            speaker = segment[2] if len(segment) > 2 else None
            return _SegmentSpec(
                start=float(segment[0]),
                end=float(segment[1]),
                speaker=speaker,
            )

        raise ValueError(
            "Каждый сегмент должен быть dict(start/end[/speaker]) "
            "или tuple/list(start, end[, speaker])"
        )

    def transcribe_segments(
        self,
        audio_path: str,
        segments: list[Any],
    ) -> list[TranscriptionSegment]:
        """Батч-транскрипция сегментов с сохранением speaker/start/end."""
        audio, sr = self._load_audio(audio_path)

        results: list[TranscriptionSegment] = []
        for raw_segment in segments:
            seg = self._parse_segment(raw_segment)
            start_index = int(seg.start * SAMPLE_RATE)
            end_index = int(seg.end * SAMPLE_RATE)
            segment_audio = audio[start_index:end_index]

            if getattr(segment_audio, "size", 0) == 0:
                transcript = ""
            else:
                wav_bytes = self._encode_wav(segment_audio, sr)
                transcript = self._send_transcription_request(wav_bytes)

            results.append(
                TranscriptionSegment(
                    text=transcript,
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker,
                )
            )

        return results
