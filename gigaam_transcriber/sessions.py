"""DB-backed refresh-session lifecycle: rotation, replay detection, revocation.

Every refresh token carries a ``jti``; only its SHA-256 hash is persisted.
Rotation marks the old row revoked and links it to the successor row inside
one request transaction, which makes tokens effectively single-use. A
replayed (already-rotated or otherwise revoked) token triggers a chain
revocation of all its descendants, killing tokens an attacker may have
copied mid-rotation.
"""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import REFRESH_TOKEN_EXPIRE_DAYS
from gigaam_transcriber.models import RefreshSession

_CHAIN_WALK_LIMIT = 1000


def hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _clip(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


async def create_refresh_session(
    db: AsyncSession,
    *,
    user_id: str,
    jti: str,
    user_agent: str | None = None,
    ip: str | None = None,
    lifetime_days: int = REFRESH_TOKEN_EXPIRE_DAYS,
) -> RefreshSession:
    session = RefreshSession(
        hashed_jti=hash_jti(jti),
        user_id=user_id,
        expires_at=datetime.utcnow() + timedelta(days=lifetime_days),
        user_agent=_clip(user_agent, 255),
        ip=_clip(ip, 64),
    )
    db.add(session)
    return session


async def get_refresh_session(db: AsyncSession, hashed_jti: str) -> RefreshSession | None:
    result = await db.execute(select(RefreshSession).where(RefreshSession.hashed_jti == hashed_jti))
    return result.scalar_one_or_none()


def is_live(session: RefreshSession, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return session.revoked_at is None and session.expires_at > now


async def rotate_refresh_session(
    db: AsyncSession,
    current: RefreshSession,
    new_jti: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> RefreshSession:
    successor = await create_refresh_session(
        db, user_id=current.user_id, jti=new_jti, user_agent=user_agent, ip=ip
    )
    current.revoked_at = datetime.utcnow()
    current.rotated_to_hashed_jti = successor.hashed_jti
    return successor


async def revoke_refresh_chain(db: AsyncSession, start: RefreshSession) -> int:
    now = datetime.utcnow()
    revoked = 0
    current = start
    for _ in range(_CHAIN_WALK_LIMIT):
        if current is None:
            break
        if current.revoked_at is None:
            current.revoked_at = now
            revoked += 1
        next_hash = current.rotated_to_hashed_jti
        if not next_hash:
            break
        result = await db.execute(
            select(RefreshSession).where(RefreshSession.hashed_jti == next_hash)
        )
        current = result.scalar_one_or_none()
    return revoked


async def revoke_all_user_sessions(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    return result.rowcount or 0
