"""ASR Provider abstraction layer — base class, enum, factory with auto-fallback."""

from __future__ import annotations

import abc
import asyncio
import enum
import logging
import os
from typing import Any

from .data_models import TranscriptionResult, TranscriptionSegment

logger = logging.getLogger(__name__)


class ASRProvider(str, enum.Enum):
    """Supported ASR backends."""

    MISTRAL = "mistral"
    LITELLM = "litellm"


class ASRProviderBase(abc.ABC):
    """Abstract base for ASR providers.

    Every concrete provider must implement the four async methods below.
    """

    @abc.abstractmethod
    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarization: bool = True,
        denoise: bool = False,
    ) -> str:
        """Transcribe a full audio file. Returns transcribed text."""

    @abc.abstractmethod
    async def transcribe_raw(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str | None = None,
    ) -> str:
        """Transcribe raw audio bytes (no pre-processing). Returns transcribed text."""

    @abc.abstractmethod
    async def transcribe_segments(
        self,
        audio_path: str,
        segments: list[Any],
        language: str | None = None,
    ) -> list[TranscriptionSegment]:
        """Transcribe pre-segmented audio."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release HTTP client resources."""


class FallbackASRProvider(ASRProviderBase):
    """Wrapper that delegates to *primary* and falls back to *secondary* on failure.

    Fallback is per-request only — the stored preference is never mutated.
    """

    async def _invoke(self, provider, method_name: str, *args, **kwargs):
        method = getattr(provider, method_name)
        if asyncio.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        return await asyncio.to_thread(method, *args, **kwargs)

    def __init__(
        self,
        primary: ASRProviderBase,
        secondary: ASRProviderBase,
        primary_name: str,
        secondary_name: str,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._primary_name = primary_name
        self._secondary_name = secondary_name

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarization: bool = True,
        denoise: bool = False,
    ) -> str:
        try:
            return await self._invoke(self._primary, "transcribe", audio_path)
        except Exception as primary_exc:
            logger.warning(
                "ASR primary provider %s failed (transcribe), falling back to %s: %s",
                self._primary_name,
                self._secondary_name,
                primary_exc,
            )
            return await self._invoke(self._secondary, "transcribe", audio_path)

    async def transcribe_raw(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str | None = None,
    ) -> str:
        try:
            return await self._invoke(self._primary, "transcribe_raw", audio_bytes, filename, language)
        except Exception as primary_exc:
            logger.warning(
                "ASR primary provider %s failed (transcribe_raw), falling back to %s: %s",
                self._primary_name,
                self._secondary_name,
                primary_exc,
            )
            return await self._invoke(self._secondary, "transcribe_raw", audio_bytes, filename, language)

    async def transcribe_segments(
        self,
        audio_path: str,
        segments: list[Any],
        language: str | None = None,
    ) -> list[TranscriptionSegment]:
        try:
            return await self._invoke(self._primary, "transcribe_segments", audio_path, segments, language)
        except Exception as primary_exc:
            logger.warning(
                "ASR primary provider %s failed (transcribe_segments), falling back to %s: %s",
                self._primary_name,
                self._secondary_name,
                primary_exc,
            )
            return await self._invoke(self._secondary, "transcribe_segments", audio_path, segments, language)

    async def close(self) -> None:
        """Close both underlying providers."""
        for provider in (self._primary, self._secondary):
            try:
                await self._invoke(provider, "close")
            except Exception:
                logger.debug("Error closing provider %s", provider, exc_info=True)


def _create_provider(name: ASRProvider, max_retries: int | None = None) -> ASRProviderBase:
    """Instantiate a concrete provider by enum value (lazy import).

    ``max_retries`` overrides the provider's built-in HTTP retry count; None
    keeps the provider default (3). Live-hints passes its session policy so
    retries have exactly one owner per request.
    """
    if name is ASRProvider.MISTRAL:
        from .mistral_client import MistralASRClient

        kwargs: dict[str, Any] = {
            "asr_url": os.getenv("ASR_URL", "https://api.mistral.ai"),
            "model": os.getenv("ASR_MODEL", "voxtral-mini-latest"),
            "api_key": os.getenv("MISTRAL_API_KEY", ""),
            "proxy": os.getenv("PROXY_URL"),
            "min_request_interval": float(os.getenv("ASR_MIN_INTERVAL", "1.0")),
        }
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        return MistralASRClient(**kwargs)
    if name is ASRProvider.LITELLM:
        from .litellm_client import LiteLLMASRClient

        if max_retries is not None:
            return LiteLLMASRClient(max_retries=max_retries)
        return LiteLLMASRClient()
    raise ValueError(f"Unknown ASR provider: {name!r}")


def get_asr_provider(
    preference: str | None = None,
    fallback: bool = True,
    max_retries: int | None = None,
) -> ASRProviderBase:
    """Factory: return an ASR provider, optionally wrapping with fallback.

    Args:
        preference: Provider name string (``"mistral"`` or ``"litellm"``).
            ``None`` defaults to ``"litellm"``.
        fallback: If *True* (default), wraps the chosen provider in a
            :class:`FallbackASRProvider` that retries with the other provider
            when the primary fails.  Per-request only — never mutates stored
            preference.
        max_retries: Optional HTTP retry count forwarded to both providers.

    Returns:
        An :class:`ASRProviderBase` instance ready for use.

    Raises:
        ASRError: If both providers fail (when fallback is enabled).
    """
    # Resolve preference → enum
    pref = (preference or "litellm").strip().lower()
    try:
        primary_enum = ASRProvider(pref)
    except ValueError:
        logger.warning("Unknown ASR provider %r, defaulting to litellm", preference)
        primary_enum = ASRProvider.LITELLM

    primary = _create_provider(primary_enum, max_retries)

    if not fallback:
        return primary

    secondary_enum = (
        ASRProvider.LITELLM if primary_enum is ASRProvider.MISTRAL else ASRProvider.MISTRAL
    )
    secondary = _create_provider(secondary_enum, max_retries)

    return FallbackASRProvider(
        primary=primary,
        secondary=secondary,
        primary_name=primary_enum.value,
        secondary_name=secondary_enum.value,
    )
