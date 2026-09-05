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
            if asyncio.iscoroutinefunction(provider.transcribe):
                text = await provider.transcribe(test_path)
            else:
                text = await asyncio.to_thread(provider.transcribe, test_path)
            elapsed = time.monotonic() - start
            results[provider_enum.value] = {
                "ok": True,
                "text_len": len(text) if isinstance(text, str) else 0,
                "elapsed": f"{elapsed:.1f}s",
                "error": None,
            }
        except Exception as exc:
            elapsed = time.monotonic() - start
            results[provider_enum.value] = {
                "ok": False,
                "text_len": 0,
                "elapsed": f"{elapsed:.1f}s",
                "error": str(exc),
            }
        finally:
            if provider is not None and hasattr(provider, "close"):
                if asyncio.iscoroutinefunction(provider.close):
                    try:
                        await provider.close()
                    except Exception:
                        pass
                else:
                    try:
                        provider.close()
                    except Exception:
                        pass

    try:
        os.unlink(test_path)
    except OSError:
        pass

    return results
