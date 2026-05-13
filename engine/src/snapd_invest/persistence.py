"""SQLAlchemy async engine + session factory.

One shared async engine per process. Sessions are short-lived and per-request
(injected via FastAPI dependency in `api.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from snapd_invest.config import Settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models. Imported by `models.py`."""


def make_engine(settings: Settings) -> Any:
    """Create the async SQLAlchemy engine.

    `echo=False` always — turn on per-query debug via a separate flag if needed.
    """
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(
        settings.db_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )


def make_session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    """Create the session factory bound to the engine."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async generator yielding a session and committing on success.

    Usage in FastAPI dependencies:

        async def get_session() -> AsyncIterator[AsyncSession]:
            async for s in session_scope(session_factory):
                yield s
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
