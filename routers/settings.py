import asyncio
import os
import tempfile
import time

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.asr_provider import ASRProvider, DEFAULT_ASR_PROVIDER, _create_provider
from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User, UserSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ASRProviderRequest(BaseModel):
    provider: ASRProvider


class ASRProviderResponse(BaseModel):
    provider: ASRProvider


@router.get("/asr-provider", response_model=ASRProviderResponse)
async def get_asr_provider(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        return ASRProviderResponse(provider=DEFAULT_ASR_PROVIDER)

    try:
        stored = ASRProvider(settings.asr_provider)
    except ValueError:
        stored = DEFAULT_ASR_PROVIDER
    return ASRProviderResponse(provider=stored)


@router.put("/asr-provider", response_model=ASRProviderResponse)
async def set_asr_provider(
    body: ASRProviderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        settings = UserSettings(user_id=user.id, asr_provider=body.provider.value)
        db.add(settings)
    else:
        settings.asr_provider = body.provider.value

    await db.flush()
    await db.commit()

    try:
        stored = ASRProvider(settings.asr_provider)
    except ValueError:
        stored = body.provider
    return ASRProviderResponse(provider=stored)


async def _close_provider(provider) -> None:
    if provider is None or not hasattr(provider, "close"):
        return
    try:
        if asyncio.iscoroutinefunction(provider.close):
            await provider.close()
        else:
            provider.close()
    except Exception:
        pass


async def _probe_provider(provider, test_path: str) -> tuple[bool, int]:
    if asyncio.iscoroutinefunction(provider.transcribe):
        text = await provider.transcribe(test_path)
    else:
        text = await asyncio.to_thread(provider.transcribe, test_path)
    return isinstance(text, str), len(text) if isinstance(text, str) else 0


@router.get("/asr-test")
async def asr_test(user: User = Depends(get_current_user)):
    """Test both ASR providers independently with a 1s silence WAV."""
    sr = 16000
    audio = np.zeros(int(sr * 1.0), dtype="float32")
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio, sr)
    test_path = tmp.name

    results = {}

    for provider_enum in (ASRProvider.MISTRAL, ASRProvider.LITELLM):
        provider = None
        start = time.monotonic()
        try:
            provider = _create_provider(provider_enum)
            ok, text_len = await _probe_provider(provider, test_path)
            results[provider_enum.value] = {
                "ok": ok,
                "text_len": text_len,
                "elapsed": f"{time.monotonic() - start:.1f}s",
                "error": None,
            }
        except Exception as exc:
            results[provider_enum.value] = {
                "ok": False,
                "text_len": 0,
                "elapsed": f"{time.monotonic() - start:.1f}s",
                "error": str(exc),
            }
        finally:
            await _close_provider(provider)

    try:
        os.unlink(test_path)
    except OSError:
        pass

    return results
