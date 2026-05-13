"""Tests for `algo_invest.portfolio`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.broker import OrderRequest, PaperBroker
from algo_invest.clock import FakeClock
from algo_invest.data import BarData, ensure_instrument, upsert_bars
from algo_invest.portfolio import build_summary, create_account, get_account_by_name


def _bar(symbol: str, ts: datetime, close: Decimal) -> BarData:
    return BarData(
        instrument_symbol=symbol,
        interval="1d",
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
    )


class TestCreateAccount:
    async def test_persists_account(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        assert account.name == "paper"
        assert account.cash == Decimal("10000")
        assert account.account_type == "paper"

    async def test_rejects_invalid_type(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        with pytest.raises(ValueError, match="account_type"):
            await create_account(db_session, fake_clock, name="x", account_type="invalid")


class TestGetAccountByName:
    async def test_returns_existing(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        created = await create_account(db_session, fake_clock, name="paper")
        found = await get_account_by_name(db_session, "paper")
        assert found is not None
        assert found.id == created.id

    async def test_returns_none_when_missing(self, db_session: AsyncSession) -> None:
        found = await get_account_by_name(db_session, "nope")
        assert found is None


class TestBuildSummary:
    async def test_empty_account(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("5000")
        )

        summary = await build_summary(db_session, account)
        assert summary.cash == Decimal("5000")
        assert summary.equity == Decimal("5000")
        assert summary.positions == []

    async def test_with_position_and_price(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[_bar("AAPL", datetime(2026, 5, 1, tzinfo=UTC), Decimal("100"))],
            source="test",
        )
        broker = PaperBroker(fake_clock)
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                limit_price=None,
                source="test",
                idempotency_key="k",
            ),
        )
        # Price moves up
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[_bar("AAPL", datetime(2026, 5, 2, tzinfo=UTC), Decimal("110"))],
            source="test",
        )

        summary = await build_summary(db_session, account)
        assert summary.cash == Decimal("9000")  # 10000 - 1000
        assert summary.equity == Decimal("10100.0000")  # 9000 + 10*110
        assert len(summary.positions) == 1
        p = summary.positions[0]
        assert p.instrument_symbol == "AAPL"
        assert p.quantity == Decimal("10")
        assert p.avg_cost == Decimal("100")
        assert p.last_price == Decimal("110")
        assert p.unrealized_pnl == Decimal("100.0000")
