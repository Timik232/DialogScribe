import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.mindmap import generate_mindmap_markdown
from gigaam_transcriber.chat import chat_with_transcript
from gigaam_transcriber.insights import (
    extract_action_items,
    generate_suggested_steps,
)
from gigaam_transcriber.summarizer import (
    LLMClient,
    generate_summary,
    get_available_models,
)
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.models import User
from gigaam_transcriber.rate_limit import user_rate_limit
from gigaam_transcriber.usage import track_usage

from routers._helpers import api_error, logger
from routers.correlation import get_correlation_id

router = APIRouter(prefix="/api", tags=["analysis"])

llm_client = LLMClient()

MAX_CHAT_CONTEXT_CHARS = 2_000_000
MAX_CHAT_MESSAGE_CHARS = 32_768
MAX_CHAT_MESSAGES = 200
MAX_MODEL_NAME_CHARS = 100


class SummaryRequest(BaseModel):
    text: str
    model: str | None = None
    template_key: str = "general"


class MindmapRequest(BaseModel):
    text: str
    model: str | None = None


class InsightsRequest(BaseModel):
    text: str
    model: str | None = None
    include_action_items: bool = True
    include_suggested_steps: bool = True


class ChatMessage(BaseModel):
    role: str = Field(max_length=64)
    content: str = Field(max_length=MAX_CHAT_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    text: str = Field(max_length=MAX_CHAT_CONTEXT_CHARS)
    model: str | None = Field(default=None, max_length=MAX_MODEL_NAME_CHARS)
    messages: list[ChatMessage] = Field(max_length=MAX_CHAT_MESSAGES)


def _ensure_llm() -> None:
    if not llm_client.config.api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY not configured",
        )


def _validate_chat_model(model: str | None) -> None:
    if model is None:
        return
    if not model.strip() or model != model.strip():
        raise HTTPException(status_code=400, detail="Invalid model identifier")
    allowed = get_available_models()
    if model not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not available on this server",
        )


@router.post("/summary")
async def post_summary(
    body: SummaryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    await check_limit(db, user.id, "llm_call")

    try:
        md_result = await generate_summary(body.text, body.template_key, llm_client, model=body.model)
        await track_usage(db, user.id, "llm_call", 1.0)
        return {"summary_markdown": md_result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise api_error(502, "upstream_unavailable", "Upstream LLM provider is unavailable") from exc
    except Exception as exc:
        logger.exception("Summary generation failed (correlation_id=%s)", get_correlation_id())
        raise api_error(500, "internal_error", "Internal server error") from exc


@router.post("/mindmap")
async def post_mindmap(
    body: MindmapRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    await check_limit(db, user.id, "llm_call")

    try:
        md_result = await asyncio.to_thread(
            generate_mindmap_markdown, body.text, llm_client, model=body.model
        )
        await track_usage(db, user.id, "llm_call", 1.0)
        return {"mindmap_markdown": md_result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise api_error(502, "upstream_unavailable", "Upstream LLM provider is unavailable") from exc
    except Exception as exc:
        logger.exception("Mindmap generation failed (correlation_id=%s)", get_correlation_id())
        raise api_error(500, "internal_error", "Internal server error") from exc


@router.post("/insights")
async def post_insights(
    body: InsightsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    await check_limit(db, user.id, "llm_call")

    result: dict = {}

    try:
        if body.include_action_items:
            result.update(
                await asyncio.to_thread(
                    extract_action_items, body.text, llm_client, model=body.model
                )
            )
        if body.include_suggested_steps:
            result.update(
                await asyncio.to_thread(
                    generate_suggested_steps, body.text, llm_client, model=body.model
                )
            )
        await track_usage(db, user.id, "llm_call", 1.0)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise api_error(502, "upstream_unavailable", "Upstream LLM provider is unavailable") from exc
    except Exception as exc:
        logger.exception("Insights extraction failed (correlation_id=%s)", get_correlation_id())
        raise api_error(500, "internal_error", "Internal server error") from exc


@router.post("/chat", dependencies=[Depends(user_rate_limit("chat"))])
async def post_chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _validate_chat_model(body.model)
    _ensure_llm()
    await check_limit(db, user.id, "llm_call")

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    try:
        result = await asyncio.to_thread(
            chat_with_transcript,
            text=body.text,
            messages=[m.model_dump() for m in body.messages],
            model=body.model,
            llm_client=llm_client,
        )
        await track_usage(db, user.id, "llm_call", 1.0, {"type": "chat"})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise api_error(502, "upstream_unavailable", "Upstream LLM provider is unavailable") from exc
    except Exception as exc:
        logger.exception("Chat failed (correlation_id=%s)", get_correlation_id())
        raise api_error(500, "internal_error", "Internal server error") from exc


@router.get("/models")
def get_models(_user: User = Depends(get_current_user)) -> dict:
    try:
        models = get_available_models()
        return {"models": [{"id": m, "name": m} for m in models]}
    except Exception as exc:
        logger.exception("Failed to list models (correlation_id=%s)", get_correlation_id())
        raise api_error(500, "internal_error", "Internal server error") from exc
