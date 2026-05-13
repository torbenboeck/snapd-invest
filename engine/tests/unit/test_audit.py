"""Tests for `snapd_invest.audit`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.audit import list_events, record_event
from snapd_invest.clock import FakeClock


class TestRecordEvent:
    async def test_persists_event(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        event = await record_event(
            db_session,
            fake_clock,
            event_type="signal_emitted",
            payload={"instrument": "AAPL", "action": "buy"},
        )
        await db_session.commit()

        assert event.id is not None
        assert event.type == "signal_emitted"
        assert event.occurred_at == fake_clock.now()
        assert "AAPL" in event.payload

    async def test_uses_clock_for_timestamp(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        fake_clock.set(datetime(2030, 6, 15, 9, 0, 0, tzinfo=UTC))
        event = await record_event(
            db_session, fake_clock, event_type="test", payload={}
        )
        assert event.occurred_at == datetime(2030, 6, 15, 9, 0, 0, tzinfo=UTC)

    async def test_rejects_empty_type(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        with pytest.raises(ValueError, match="event_type"):
            await record_event(db_session, fake_clock, event_type="", payload={})

    async def test_rejects_non_json_payload(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        class NotSerializable:
            pass

        with pytest.raises(ValueError, match="JSON-encodable"):
            await record_event(
                db_session,
                fake_clock,
                event_type="test",
                payload={"bad": NotSerializable()},  # type: ignore[dict-item]
            )

    async def test_correlation_id_persisted(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        event = await record_event(
            db_session,
            fake_clock,
            event_type="test",
            payload={},
            correlation_id="abc-123",
        )
        assert event.correlation_id == "abc-123"


class TestListEvents:
    async def test_returns_newest_first(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        await record_event(db_session, fake_clock, event_type="a", payload={})
        fake_clock.advance(seconds=1)
        await record_event(db_session, fake_clock, event_type="b", payload={})
        fake_clock.advance(seconds=1)
        await record_event(db_session, fake_clock, event_type="c", payload={})
        await db_session.commit()

        events = await list_events(db_session)
        assert [e.type for e in events] == ["c", "b", "a"]

    async def test_filters_by_type(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        await record_event(db_session, fake_clock, event_type="signal", payload={})
        fake_clock.advance(seconds=1)
        await record_event(db_session, fake_clock, event_type="order", payload={})
        await db_session.commit()

        events = await list_events(db_session, event_type="signal")
        assert len(events) == 1
        assert events[0].type == "signal"

    async def test_filters_by_correlation_id(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        await record_event(
            db_session, fake_clock, event_type="a", payload={}, correlation_id="x"
        )
        await record_event(
            db_session, fake_clock, event_type="b", payload={}, correlation_id="y"
        )
        await db_session.commit()

        events = await list_events(db_session, correlation_id="x")
        assert len(events) == 1
        assert events[0].type == "a"

    async def test_filters_by_since(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        await record_event(db_session, fake_clock, event_type="old", payload={})
        cutoff = fake_clock.now()
        fake_clock.advance(seconds=10)
        await record_event(db_session, fake_clock, event_type="new", payload={})
        await db_session.commit()

        events = await list_events(db_session, since=cutoff)
        types = [e.type for e in events]
        # cutoff is >= old's occurred_at, so old is included
        assert "new" in types

    async def test_respects_limit(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        for _ in range(5):
            await record_event(db_session, fake_clock, event_type="t", payload={})
            fake_clock.advance(seconds=1)
        await db_session.commit()

        events = await list_events(db_session, limit=3)
        assert len(events) == 3

    async def test_rejects_invalid_limit(self, db_session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="limit"):
            await list_events(db_session, limit=0)
        with pytest.raises(ValueError, match="limit"):
            await list_events(db_session, limit=1001)
