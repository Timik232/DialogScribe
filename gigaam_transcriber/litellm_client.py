"""LiteLLM ASR client — talks to an OpenAI-compatible /v1/audio/transcriptions endpoint."""

from __future__ import annotations

import asyncio
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
    def __init__(self) -> None:
        self._base_url = os.getenv("LITELLM_URL", _DEFAULT_URL).rstrip("/")
        self._model = os.getenv("LITELLM_MODEL", _DEFAULT_MODEL)
        self._api_key = os.getenv("LITELLM_API_KEY", os.getenv("LLM_API_KEY", ""))
        self._timeout = 300.0

    async def close(self) -> None:
        # No persistent client to close: each request opens its own
        # httpx.AsyncClient bound to the currently-running event loop.
        # The transcriber drives ASR via repeated asyncio.run() calls
        # (each creating and then closing a fresh loop), so a client held
        # across calls would raise "Event loop is closed" on reuse.
        return None

    async def _post_transcription(
        self,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, Any],
    ) -> str:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/v1/audio/transcriptions"

        for attempt in range(_MAX_RETRIES + 1):
            try:
                # Fresh client per attempt — binds to the current event loop.
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url, headers=headers, files=files, data=data,
                    )
                    response.raise_for_status()
                    payload = response.json()
                return payload.get("text", "") or ""
            except httpx.TimeoutException as exc:
                if attempt == _MAX_RETRIES:
                    raise ASRError(
                        f"LiteLLM ASR timeout after {_MAX_RETRIES} retries", cause=exc,
                    ) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "LiteLLM timeout (attempt %d/%d), retry in %.1fs",
                    attempt + 1, _MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < _MAX_RETRIES:
                    backoff = min(1.0 * (2.0 ** attempt), 10.0)
                    logger.warning(
                        "LiteLLM status %d (attempt %d/%d), retry in %.1fs",
                        exc.response.status_code, attempt + 1, _MAX_RETRIES, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise ASRError(
                    f"LiteLLM HTTP {exc.response.status_code}: {exc.response.text}",
                    cause=exc,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == _MAX_RETRIES:
                    raise ASRError(
                        f"LiteLLM request error after {_MAX_RETRIES} retries", cause=exc,
                    ) from exc
                backoff = min(1.0 * (2.0 ** attempt), 10.0)
                logger.warning(
                    "LiteLLM request error (attempt %d/%d), retry in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, backoff, exc,
                )
                await asyncio.sleep(backoff)
                continue

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
        results: list[TranscriptionSegment] = []
        with open(audio_path, "rb") as f:
            raw = f.read()

        ext = audio_path.rsplit(".", 1)[-1] if "." in audio_path else "wav"

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

            files = {"file": (f"audio.{ext}", raw, "application/octet-stream")}
            data: dict[str, Any] = {"model": self._model}
            if language:
                data["language"] = language

            text = await self._post_transcription(files, data)
            results.append(
                TranscriptionSegment(text=text, start=start, end=end, speaker=speaker),
            )

        return results
