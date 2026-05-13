"""Tests for `snapd_invest.broker.PaperBroker`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.broker import OrderRequest, PaperBroker
from snapd_invest.clock import FakeClock
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.models import Position
from snapd_invest.portfolio import create_account


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


async def _setup_environment(
    session: AsyncSession, clock: FakeClock, *, last_price: Decimal
) -> tuple[object, object]:
    account = await create_account(
        session, clock, name="paper-main", initial_cash=Decimal("100000")
    )
    instrument = await ensure_instrument(
        session,
        symbol="AAPL",
        exchange="NASDAQ",
        instrument_type="stock",
        currency="USD",
    )
    await upsert_bars(
        session,
        instrument=instrument,
        bars=[_bar("AAPL", datetime(2026, 5, 1, tzinfo=UTC), last_price)],
        source="test",
    )
    return account, instrument


class TestPaperBrokerBuy:
    async def test_market_buy_fills_at_last_price(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
        )
        broker = PaperBroker(fake_clock)

        result = await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                limit_price=None,
                source="test",
                idempotency_key="key-1",
            ),
        )

        assert result.order.status == "filled"
        assert len(result.trades) == 1
        assert result.trades[0].fill_price == Decimal("150")
        assert result.trades[0].fill_quantity == Decimal("10")

    async def test_buy_updates_position(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
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
                idempotency_key="key-1",
            ),
        )

        position = (
            await db_session.execute(
                select(Position).where(
                    Position.account_id == account.id,
                    Position.instrument_id == instrument.id,
                )
            )
        ).scalar_one()
        assert position.quantity == Decimal("10")
        assert position.avg_cost == Decimal("150")

    async def test_buy_deducts_cash(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
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
                idempotency_key="key-1",
            ),
        )

        assert account.cash == Decimal("98500")  # 100000 - 1500

    async def test_buy_recomputes_avg_cost(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("100")
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
                idempotency_key="k1",
            ),
        )
        # New bar at higher price
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[_bar("AAPL", datetime(2026, 5, 2, tzinfo=UTC), Decimal("200"))],
            source="test",
        )
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                limit_price=None,
                source="test",
                idempotency_key="k2",
            ),
        )
        position = (
            await db_session.execute(
                select(Position).where(Position.account_id == account.id)
            )
        ).scalar_one()
        assert position.quantity == Decimal("20")
        assert position.avg_cost == Decimal("150")  # (10*100 + 10*200) / 20


class TestPaperBrokerSell:
    async def test_sell_reduces_position(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
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
                idempotency_key="buy-1",
            ),
        )
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("4"),
                limit_price=None,
                source="test",
                idempotency_key="sell-1",
            ),
        )
        position = (
            await db_session.execute(
                select(Position).where(Position.account_id == account.id)
            )
        ).scalar_one()
        assert position.quantity == Decimal("6")

    async def test_sell_returns_cash(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
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
                idempotency_key="buy-1",
            ),
        )
        cash_after_buy = account.cash
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("4"),
                limit_price=None,
                source="test",
                idempotency_key="sell-1",
            ),
        )
        assert account.cash == cash_after_buy + Decimal("600")  # 4 * 150


class TestPaperBrokerLimitOrders:
    async def test_buy_limit_fills_when_marketable(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
        )
        broker = PaperBroker(fake_clock)

        result = await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                limit_price=Decimal("160"),  # above last - marketable
                source="test",
                idempotency_key="k",
            ),
        )
        assert result.order.status == "filled"
        assert result.trades[0].fill_price == Decimal("160")

    async def test_buy_limit_rejected_when_unfavorable(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
        )
        broker = PaperBroker(fake_clock)

        result = await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                limit_price=Decimal("140"),  # last > limit, not marketable
                source="test",
                idempotency_key="k",
            ),
        )
        assert result.order.status == "rejected"
        assert result.trades == []


class TestIdempotency:
    async def test_replay_returns_same_order(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument = await _setup_environment(
            db_session, fake_clock, last_price=Decimal("150")
        )
        broker = PaperBroker(fake_clock)
        req = OrderRequest(
            account=account,
            instrument=instrument,
            side="buy",
            quantity=Decimal("10"),
            limit_price=None,
            source="test",
            idempotency_key="dup-key",
        )

        first = await broker.place_order(db_session, req)
        second = await broker.place_order(db_session, req)

        assert first.order.id == second.order.id
        assert second.was_idempotent_replay is True


class TestNoPriceData:
    async def test_rejects_when_no_bars(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        instrument = await ensure_instrument(
            db_session,
            symbol="NOBARS",
            exchange="X",
            instrument_type="stock",
            currency="USD",
        )
        broker = PaperBroker(fake_clock)

        result = await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("1"),
                limit_price=None,
                source="test",
                idempotency_key="k",
            ),
        )
        assert result.order.status == "rejected"
