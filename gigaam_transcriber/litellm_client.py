"""LiteLLM ASR client — talks to an OpenAI-compatible /v1/audio/transcriptions endpoint."""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import os
from typing import Any

import httpx

from .asr_provider import ASRProviderBase
from .data_models import TranscriptionResult, TranscriptionSegment
from .exceptions import ASRError

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://litellm.komolov.synology.me"
_DEFAULT_MODEL = "gigaamv3-generation"
_MAX_RETRIES = 3


class LiteLLMASRClient(ASRProviderBase):
    def __init__(self, max_retries: int | None = None) -> None:
        self._base_url = os.getenv("LITELLM_URL", _DEFAULT_URL).rstrip("/")
        self._model = os.getenv("LITELLM_MODEL", _DEFAULT_MODEL)
        self._api_key = os.getenv("LITELLM_API_KEY", os.getenv("LLM_API_KEY", ""))
        self._client = httpx.AsyncClient(timeout=300.0)
        self._max_retries = _MAX_RETRIES if max_retries is None else max(0, int(max_retries))

    async def close(self) -> None:
        await self._client.aclose()

    async def _post_transcription(
        self,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, Any],
    ) -> str:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/v1/audio/transcriptions"

        max_retries = self._max_retries
        for attempt in range(max_retries + 1):
            try:
                response = await self._client.post(
                    url, headers=headers, files=files, data=data,
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                if attempt == max_retries:
                    raise ASRError(
                        f"LiteLLM ASR timeout after {max_retries} retries", cause=exc,
                    ) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "LiteLLM timeout (attempt %d/%d), retry in %.1fs",
                    attempt + 1, max_retries, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < max_retries:
                    backoff = min(1.0 * (2.0 ** attempt), 10.0)
                    logger.warning(
                        "LiteLLM status %d (attempt %d/%d), retry in %.1fs",
                        exc.response.status_code, attempt + 1, max_retries, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise ASRError(
                    f"LiteLLM HTTP {exc.response.status_code}: {exc.response.text}",
                    cause=exc,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == max_retries:
                    raise ASRError(
                        f"LiteLLM request error after {max_retries} retries", cause=exc,
                    ) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "LiteLLM request error (attempt %d/%d), retry in %.1fs: %s",
                    attempt + 1, max_retries, backoff, exc,
                )
                await asyncio.sleep(backoff)
                continue

            payload = response.json()
            return payload.get("text", "") or ""

        raise ASRError("LiteLLM ASR: exhausted retries")

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarization: bool = True,
        denoise: bool = False,
    ) -> str:
        with open(audio_path, "rb") as f:
            raw = f.read()

        ext = audio_path.rsplit(".", 1)[-1] if "." in audio_path else "wav"
        files = {"file": (f"audio.{ext}", raw, "application/octet-stream")}
        data: dict[str, Any] = {"model": self._model}
        if language:
            data["language"] = language

        return await self._post_transcription(files, data)

    async def transcribe_raw(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str | None = None,
    ) -> str:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "wav"
        files = {"file": (filename, audio_bytes, f"audio/{ext}")}
        data: dict[str, Any] = {"model": self._model}
        if language:
            data["language"] = language

        return await self._post_transcription(files, data)

    async def transcribe_segments(
        self,
        audio_path: str,
        segments: list[Any],
        language: str | None = None,
    ) -> list[TranscriptionSegment]:
        sf = importlib.import_module("soundfile")

        audio, sr = sf.read(audio_path, always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)

        results: list[TranscriptionSegment] = []
        for seg in segments:
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                speaker = seg.get("speaker")
            elif isinstance(seg, (list, tuple)) and len(seg) >= 2:
                start = float(seg[0])
                end = float(seg[1])
                speaker = seg[2] if len(seg) > 2 else None
            else:
                raise ValueError(f"Invalid segment format: {seg!r}")

            segment_audio = audio[int(start * sr):int(end * sr)]
            if getattr(segment_audio, "size", 0) == 0:
                results.append(
                    TranscriptionSegment(text="", start=start, end=end, speaker=speaker),
                )
                continue

            buffer = io.BytesIO()
            sf.write(buffer, segment_audio, sr, format="WAV")
            files = {"file": ("audio.wav", buffer.getvalue(), "application/octet-stream")}
            data: dict[str, Any] = {"model": self._model}
            if language:
                data["language"] = language

            text = await self._post_transcription(files, data)
            results.append(
                TranscriptionSegment(text=text, start=start, end=end, speaker=speaker),
            )

        return results
