import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.mindmap import (
    generate_mindmap_markdown,
    render_mindmap_html,
)
from gigaam_transcriber.chat import chat_with_transcript
from gigaam_transcriber.insights import (
    extract_action_items,
    generate_suggested_steps,
)
from gigaam_transcriber.summarizer import (
    LLMClient,
    generate_summary,
    get_available_models,
    summary_to_html,
)
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.models import User
from gigaam_transcriber.usage import track_usage

from routers._helpers import logger

router = APIRouter(prefix="/api", tags=["analysis"])

llm_client = LLMClient()


class MeetingPrepRequest(BaseModel):
    company_data: str
    catalog_data: str
    model: str | None = None


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
    role: str
    content: str


class ChatRequest(BaseModel):
    text: str
    model: str | None = None
    messages: list[ChatMessage]


def _ensure_llm() -> None:
    if not llm_client.config.api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY not configured",
        )


def _maybe_update_model(model: str | None) -> None:
    if model and model != llm_client.config.model:
        llm_client.update_config(
            llm_client.config.base_url,
            llm_client.config.api_key,
            model,
        )


@router.post("/summary")
async def post_summary(
    body: SummaryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    _maybe_update_model(body.model)
    await check_limit(db, user.id, "llm_call")

    try:
        md_result = await generate_summary(body.text, body.template_key, llm_client)
        html_result = summary_to_html(md_result)
        await track_usage(db, user.id, "llm_call", 1.0)
        return {"summary_markdown": md_result, "summary_html": html_result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Summary generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mindmap")
async def post_mindmap(
    body: MindmapRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    _maybe_update_model(body.model)
    await check_limit(db, user.id, "llm_call")

    try:
        md_result = generate_mindmap_markdown(body.text, llm_client)
        uid = uuid.uuid4().hex[:12]
        mindmap_html = render_mindmap_html(md_result, uid=uid)
        await track_usage(db, user.id, "llm_call", 1.0)
        return {"mindmap_markdown": md_result, "mindmap_uid": uid, "mindmap_html": mindmap_html}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Mindmap generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/insights")
async def post_insights(
    body: InsightsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    _maybe_update_model(body.model)
    await check_limit(db, user.id, "llm_call")

    result: dict = {}

    try:
        if body.include_action_items:
            result.update(extract_action_items(body.text, llm_client))
        if body.include_suggested_steps:
            result.update(generate_suggested_steps(body.text, llm_client))
        await track_usage(db, user.id, "llm_call", 1.0)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Insights extraction failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/chat")
async def post_chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    _maybe_update_model(body.model)
    await check_limit(db, user.id, "llm_call")

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    try:
        result = chat_with_transcript(
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
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Chat failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/models")
def get_models(_user: User = Depends(get_current_user)) -> dict:
    try:
        models = get_available_models()
        return {"models": [{"id": m, "name": m} for m in models]}
    except Exception as exc:
        logger.exception("Failed to list models")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


MEETING_PREP_SYSTEM_PROMPT = (
    "Ты — эксперт по подготовке к B2B-встречам. На основе данных о компании-клиенте "
    "и каталога продуктов/услуг составь детальный план подготовки к встрече на русском языке.\n\n"
    "Структура ответа:\n"
    "## Профиль компании\n"
    "Краткая сводка: отрасль, размер, ключевые факты.\n\n"
    "## Потенциальные потребности\n"
    "Какие проблемы/задачи компании могут решить наши продукты. Обоснуй связь.\n\n"
    "## Рекомендуемые продукты\n"
    "| Продукт | Потребность клиента | Аргумент |\n"
    "|---------|---------------------|----------|\n\n"
    "## Ключевые вопросы для встречи\n"
    "Список вопросов, которые стоит задать клиенту для квалификации.\n\n"
    "## Возможные возражения и ответы\n"
    "- **Возражение**: ... → **Ответ**: ...\n\n"
    "## Стратегия встречи\n"
    "Рекомендуемый сценарий: с чего начать, на чём сделать акцент, как завершить.\n\n"
    "Правила:\n"
    "- Опирайся только на предоставленные данные, не выдумывай\n"
    "- Будь конкретен и практичен\n"
    "- Формат: Markdown"
)


@router.post("/meeting-prep")
async def post_meeting_prep(
    body: MeetingPrepRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_llm()
    _maybe_update_model(body.model)
    await check_limit(db, user.id, "llm_call")

    user_text = (
        f"## Данные о компании-клиенте\n{body.company_data}\n\n"
        f"## Каталог наших продуктов/услуг\n{body.catalog_data}"
    )

    try:
        markdown = llm_client.call(MEETING_PREP_SYSTEM_PROMPT, user_text)
        await track_usage(db, user.id, "llm_call", 1.0, {"type": "meeting_prep"})
        return {
            "id": uuid.uuid4().hex[:12],
            "markdown": markdown,
            "model": llm_client.config.model,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Meeting prep generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
