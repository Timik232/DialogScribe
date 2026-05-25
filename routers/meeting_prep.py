import uuid

from fastapi import APIRouter, Depends, HTTPException

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.limits import check_limit
from gigaam_transcriber.models import User
from gigaam_transcriber.summarizer import LLMClient
from gigaam_transcriber.usage import track_usage
from gigaam_transcriber.meeting_prep.schemas import MeetingPrepRequest, MeetingPrepResponse
from gigaam_transcriber.meeting_prep.service import generate_meeting_prep
from routers._helpers import logger

router = APIRouter(prefix="/api", tags=["meeting-prep"])

llm_client = LLMClient()


def _ensure_llm() -> None:
    if not llm_client.config.api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY not configured")


@router.post("/meeting-prep", response_model=MeetingPrepResponse)
async def post_meeting_prep(
    body: MeetingPrepRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    _ensure_llm()
    await check_limit(db, user.id, "llm_call")

    try:
        model_override = body.model if body.model and body.model != llm_client.config.model else None
        result, model_used = await generate_meeting_prep(
            body.company_data, body.catalog_data, llm_client,
            model_override=model_override,
        )
        await track_usage(db, user.id, "llm_call", 1.0)
        return MeetingPrepResponse(
            id=str(uuid.uuid4()),
            markdown=result,
            model=model_used,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail="LLM service unavailable") from exc
    except Exception as exc:
        logger.exception("Meeting prep generation failed")
        raise HTTPException(status_code=500, detail="Meeting prep generation failed") from exc
