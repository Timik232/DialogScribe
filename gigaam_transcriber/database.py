import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./data/dialogscribe.db"

is_sqlite = DATABASE_URL.startswith("sqlite")

engine_kwargs = {"echo": False}
if not is_sqlite:
    engine_kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True)

engine = create_async_engine(DATABASE_URL, **engine_kwargs)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
