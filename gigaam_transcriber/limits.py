from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.models import UsageEvent, UserLimit


async def get_user_limits(db: AsyncSession, user_id: str) -> list[dict]:
    result = await db.execute(
        select(UserLimit).where(UserLimit.user_id == user_id)
    )
    limits = result.scalars().all()
    return [
        {
            "limit_type": lim.limit_type,
            "max_value": lim.max_value,
            "period": lim.period,
            "enabled": lim.enabled,
        }
        for lim in limits
    ]


async def get_remaining_quota(db: AsyncSession, user_id: str, limit_type: str) -> float | None:
    result = await db.execute(
        select(UserLimit).where(
            UserLimit.user_id == user_id,
            UserLimit.limit_type == limit_type,
        )
    )
    limit = result.scalar_one_or_none()
    if not limit or not limit.enabled:
        return None

    now = datetime.utcnow()
    if limit.period == "daily":
        since = now - timedelta(days=1)
    elif limit.period == "monthly":
        since = now - timedelta(days=30)
    else:
        since = now - timedelta(days=30)

    usage_result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.value), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type == limit_type,
            UsageEvent.created_at >= since,
        )
    )
    current_usage = float(usage_result.scalar())
    return limit.max_value - current_usage


async def check_limit(db: AsyncSession, user_id: str, limit_type: str) -> None:
    result = await db.execute(
        select(UserLimit).where(
            UserLimit.user_id == user_id,
            UserLimit.limit_type == limit_type,
        )
    )
    limit = result.scalar_one_or_none()

    if not limit or not limit.enabled:
        return

    now = datetime.utcnow()
    if limit.period == "daily":
        since = now - timedelta(days=1)
    elif limit.period == "monthly":
        since = now - timedelta(days=30)
    else:
        since = now - timedelta(days=30)

    usage_result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.value), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type == limit_type,
            UsageEvent.created_at >= since,
        )
    )
    current_usage = float(usage_result.scalar())

    if current_usage >= limit.max_value:
        raise HTTPException(
            status_code=429,
            detail=f"Usage limit exceeded for {limit_type}. Limit: {limit.max_value}, Used: {current_usage:.2f}",
        )
