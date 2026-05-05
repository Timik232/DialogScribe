from datetime import datetime

from sqlalchemy import func, select, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.models import UsageEvent, User


async def track_usage(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    value: float = 1.0,
    metadata: dict | None = None,
) -> UsageEvent:
    event = UsageEvent(
        user_id=user_id,
        event_type=event_type,
        value=value,
        metadata_=metadata or {},
    )
    db.add(event)
    await db.flush()
    return event


async def get_usage_stats(
    db: AsyncSession,
    user_id: str,
    since: datetime | None = None,
) -> list[dict]:
    query = (
        select(UsageEvent.event_type, func.sum(UsageEvent.value).label("total"), func.count().label("count"))
        .where(UsageEvent.user_id == user_id)
        .group_by(UsageEvent.event_type)
    )
    if since:
        query = query.where(UsageEvent.created_at >= since)

    result = await db.execute(query)
    return [
        {"event_type": row.event_type, "total": float(row.total), "count": row.count}
        for row in result.all()
    ]


async def get_global_stats(
    db: AsyncSession,
    since: datetime | None = None,
) -> dict:
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar()
    active_users = (await db.execute(select(func.count()).select_from(User).where(User.is_active == True))).scalar()
    pending_users = (await db.execute(
        select(func.count()).select_from(User).where(User.is_active == False, User.approved_at.is_(None))
    )).scalar()

    usage_query = select(
        func.sum(UsageEvent.value).label("total_value"),
        func.count().label("total_count"),
    )
    if since:
        usage_query = usage_query.where(UsageEvent.created_at >= since)
    usage_row = (await db.execute(usage_query)).one()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "pending_users": pending_users,
        "total_usage_value": float(usage_row.total_value or 0),
        "total_usage_count": usage_row.total_count or 0,
    }


async def get_usage_timeseries(
    db: AsyncSession,
    since: datetime | None = None,
) -> list[dict]:
    query = select(
        cast(UsageEvent.created_at, Date).label("date"),
        UsageEvent.event_type,
        func.sum(UsageEvent.value).label("total"),
        func.count().label("count"),
    ).group_by(
        cast(UsageEvent.created_at, Date),
        UsageEvent.event_type,
    ).order_by(
        cast(UsageEvent.created_at, Date),
        UsageEvent.event_type,
    )
    if since:
        query = query.where(UsageEvent.created_at >= since)

    result = await db.execute(query)
    return [
        {"date": str(row.date), "event_type": row.event_type, "total": float(row.total), "count": row.count}
        for row in result.all()
    ]
