from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.limits import get_remaining_quota, get_user_limits
from gigaam_transcriber.models import User
from gigaam_transcriber.usage import get_usage_stats

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/me")
async def get_my_usage(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    period: str = Query("monthly", pattern="^(daily|monthly|all)$"),
) -> dict:
    now = datetime.utcnow()
    if period == "daily":
        since = now - timedelta(days=1)
    elif period == "monthly":
        since = now - timedelta(days=30)
    else:
        since = None

    stats = await get_usage_stats(db, user.id, since)

    limits_raw = await get_user_limits(db, user.id)
    limits = []
    for lim in limits_raw:
        remaining = await get_remaining_quota(db, user.id, lim["limit_type"])
        limits.append({
            **lim,
            "remaining": remaining,
        })

    return {"period": period, "stats": stats, "limits": limits}
