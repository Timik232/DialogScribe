from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_admin_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import UsageEvent, User, UserLimit
from gigaam_transcriber.usage import get_global_stats, get_usage_timeseries

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserPatchRequest(BaseModel):
    is_active: bool | None = None


class LimitPutRequest(BaseModel):
    limit_type: str
    max_value: float
    period: str = "monthly"
    enabled: bool = True


@router.get("/stats/overview")
async def stats_overview(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await get_global_stats(db)


@router.get("/stats/timeseries")
async def stats_timeseries(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(14, ge=1, le=365),
) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    data = await get_usage_timeseries(db, since)
    return {"since": since.isoformat(), "days": days, "data": data}


@router.get("/users")
async def list_users(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    search: str | None = Query(None),
    status: str = Query("all", pattern="^(all|pending|active|disabled)$"),
) -> dict:
    query = select(User).order_by(User.created_at.desc())

    if search:
        term = f"%{search}%"
        query = query.where(
            or_(
                User.email.ilike(term),
                User.username.ilike(term),
            )
        )

    if status == "pending":
        query = query.where(User.is_active == False, User.approved_at.is_(None))
    elif status == "active":
        query = query.where(User.is_active == True)
    elif status == "disabled":
        query = query.where(User.is_active == False, User.approved_at.is_not(None))

    result = await db.execute(query)
    users = result.scalars().all()

    user_ids = [u.id for u in users]
    usage_map: dict[str, list[dict]] = {}
    if user_ids:
        usage_result = await db.execute(
            select(
                UsageEvent.user_id,
                UsageEvent.event_type,
                func.sum(UsageEvent.value).label("total"),
                func.count().label("count"),
            )
            .where(
                UsageEvent.user_id.in_(user_ids),
                UsageEvent.created_at >= datetime.utcnow() - timedelta(days=30),
            )
            .group_by(UsageEvent.user_id, UsageEvent.event_type)
        )
        for row in usage_result.all():
            usage_map.setdefault(row.user_id, []).append(
                {"event_type": row.event_type, "total": float(row.total), "count": row.count}
            )

    users_data = [
        {
            "id": u.id,
            "email": u.email,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "approved_at": u.approved_at.isoformat() if u.approved_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "usage_summary": usage_map.get(u.id, []),
        }
        for u in users
    ]

    return {"users": users_data}


@router.patch("/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    user.approved_by = admin.id
    user.approved_at = datetime.utcnow()
    await db.flush()

    return {
        "id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "approved_at": user.approved_at.isoformat() if user.approved_at else None,
    }


@router.delete("/users/{user_id}")
async def reject_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_active:
        raise HTTPException(status_code=400, detail="Cannot delete active user")

    await db.delete(user)
    await db.flush()
    return {"detail": "User rejected and deleted"}


@router.get("/users/{user_id}/usage")
async def get_user_usage(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    period: str = "monthly",
) -> dict:
    now = datetime.utcnow()
    if period == "daily":
        since = now - timedelta(days=1)
    elif period == "monthly":
        since = now - timedelta(days=30)
    else:
        since = None

    from gigaam_transcriber.usage import get_usage_stats
    stats = await get_usage_stats(db, user_id, since)
    return {"user_id": user_id, "period": period, "stats": stats}


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_active is not None:
        user.is_active = body.is_active

    await db.flush()
    return {"id": user.id, "email": user.email, "is_active": user.is_active}


@router.put("/users/{user_id}/limits")
async def put_user_limit(
    user_id: str,
    body: LimitPutRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = await db.execute(
        select(UserLimit).where(
            UserLimit.user_id == user_id,
            UserLimit.limit_type == body.limit_type,
        )
    )
    limit = existing.scalar_one_or_none()

    if limit:
        limit.max_value = body.max_value
        limit.period = body.period
        limit.enabled = body.enabled
    else:
        limit = UserLimit(
            user_id=user_id,
            limit_type=body.limit_type,
            max_value=body.max_value,
            period=body.period,
            enabled=body.enabled,
        )
        db.add(limit)

    await db.flush()
    return {
        "id": limit.id,
        "user_id": user_id,
        "limit_type": limit.limit_type,
        "max_value": limit.max_value,
        "period": limit.period,
        "enabled": limit.enabled,
    }
