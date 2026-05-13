"""Shared pytest fixtures.

In-memory SQLite for tests — no file system dependency, fast, isolated.
Each test gets its own engine + session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from snapd_invest.clock import FakeClock
from snapd_invest.persistence import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def db_engine() -> AsyncIterator[object]:
    """Per-test in-memory SQLite engine with schema applied."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: object) -> AsyncIterator[AsyncSession]:
    """Per-test session. The test is responsible for committing if it cares."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def fake_clock() -> FakeClock:
    """A FakeClock fixed at 2026-05-12 12:00:00 UTC by default."""
    return FakeClock(initial=datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC))
