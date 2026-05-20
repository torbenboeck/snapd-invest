"""Tests for `snapd_invest.portfolio`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from snapd_invest.audit import list_events
from snapd_invest.broker import OrderRequest, PaperBroker
from snapd_invest.broker.saxo import SaxoPosition
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.models import Instrument, Position, new_id
from snapd_invest.portfolio import (
    build_summary,
    create_account,
    get_account_by_name,
    reconcile_sim_positions,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker.saxo import SaxoBroker as _SaxoBrokerType
    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account


class _FakeSaxoBroker:
    """Duck-typed stand-in for SaxoBroker — only `get_positions` is exercised."""

    def __init__(self, positions: list[SaxoPosition]) -> None:
        self._positions = positions

    async def get_positions(self, session: AsyncSession, *, account: Account) -> list[SaxoPosition]:
        _ = (session, account)
        return self._positions


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
    async def test_persists_account(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
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
    async def test_returns_existing(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        created = await create_account(db_session, fake_clock, name="paper")
        found = await get_account_by_name(db_session, "paper")
        assert found is not None
        assert found.id == created.id

    async def test_returns_none_when_missing(self, db_session: AsyncSession) -> None:
        found = await get_account_by_name(db_session, "nope")
        assert found is None


class TestBuildSummary:
    async def test_empty_account(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
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


async def _seed_sim_account_with_position(
    db_session: AsyncSession,
    fake_clock: FakeClock,
    *,
    quantity: Decimal,
    avg_cost: Decimal,
    saxo_uic: int = 16,
) -> tuple[Account, Instrument, Position]:
    account = await create_account(
        db_session, fake_clock, name="sim", account_type="sim", base_currency="DKK"
    )
    account.saxo_client_key = "client-key-abc"
    account.saxo_account_key = "acc-key-1"
    instrument = Instrument(
        id=new_id(),
        symbol="EURDKK",
        exchange="FX",
        instrument_type="fx",
        currency="DKK",
        tick_size=Decimal("0.00001"),
        saxo_uic=saxo_uic,
        saxo_asset_type="FxSpot",
    )
    db_session.add(instrument)
    position = Position(
        id=new_id(),
        account_id=account.id,
        instrument_id=instrument.id,
        quantity=quantity,
        avg_cost=avg_cost,
        tag="managed",
        updated_at=fake_clock.now(),
    )
    db_session.add(position)
    await db_session.flush()
    return account, instrument, position


def _saxo_pos(
    *,
    uic: int = 16,
    symbol: str = "EURDKK",
    amount: Decimal = Decimal("100000"),
    open_price: Decimal = Decimal("7.47"),
    currency: str = "DKK",
) -> SaxoPosition:
    return SaxoPosition(
        uic=uic,
        symbol=symbol,
        asset_type="FxSpot",
        amount=amount,
        open_price=open_price,
        currency=currency,
    )


def _saxo_broker_with(positions: list[SaxoPosition]) -> _SaxoBrokerType:
    return _FakeSaxoBroker(positions)  # type: ignore[return-value]


class TestReconcileSimPositions:
    async def test_match_is_noop(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account, _, position = await _seed_sim_account_with_position(
            db_session, fake_clock, quantity=Decimal("100000"), avg_cost=Decimal("7.47")
        )
        broker = _saxo_broker_with([_saxo_pos()])

        await reconcile_sim_positions(db_session, fake_clock, broker, account)

        await db_session.refresh(position)
        assert position.quantity == Decimal("100000")
        assert position.avg_cost == Decimal("7.47")
        events = await list_events(db_session, event_type="position_drift")
        assert events == []

    async def test_drift_updates_engine_row_and_audits(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, position = await _seed_sim_account_with_position(
            db_session, fake_clock, quantity=Decimal("100000"), avg_cost=Decimal("7.47")
        )
        broker = _saxo_broker_with([_saxo_pos(amount=Decimal("80000"), open_price=Decimal("7.50"))])

        await reconcile_sim_positions(db_session, fake_clock, broker, account)

        await db_session.refresh(position)
        assert position.quantity == Decimal("80000")
        assert position.avg_cost == Decimal("7.50")
        events = list(await list_events(db_session, event_type="position_drift"))
        assert len(events) == 1
        payload = json.loads(events[0].payload)
        assert payload["saxo_uic"] == 16
        assert payload["engine_quantity"] == "100000"
        assert payload["saxo_amount"] == "80000"

    async def test_new_position_creates_view_only_row_and_audits(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="sim", account_type="sim", base_currency="DKK"
        )
        broker = _saxo_broker_with(
            [
                _saxo_pos(
                    uic=21,
                    symbol="EURUSD",
                    amount=Decimal("50000"),
                    open_price=Decimal("1.08"),
                    currency="USD",
                )
            ]
        )

        await reconcile_sim_positions(db_session, fake_clock, broker, account)

        positions = (
            (await db_session.execute(select(Position).where(Position.account_id == account.id)))
            .scalars()
            .all()
        )
        assert len(positions) == 1
        assert positions[0].tag == "view_only"
        assert positions[0].quantity == Decimal("50000")
        assert positions[0].avg_cost == Decimal("1.08")
        # Instrument auto-created with saxo_uic populated.
        instrument = (
            await db_session.execute(select(Instrument).where(Instrument.saxo_uic == 21))
        ).scalar_one()
        assert instrument.symbol == "EURUSD"
        assert instrument.saxo_asset_type == "FxSpot"

        events = list(await list_events(db_session, event_type="position_view_only_created"))
        assert len(events) == 1

    async def test_gone_position_zeroed_and_audited(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, position = await _seed_sim_account_with_position(
            db_session, fake_clock, quantity=Decimal("100000"), avg_cost=Decimal("7.47")
        )
        # Saxo reports no positions.
        broker = _saxo_broker_with([])

        await reconcile_sim_positions(db_session, fake_clock, broker, account)

        await db_session.refresh(position)
        assert position.quantity == Decimal("0")
        events = list(await list_events(db_session, event_type="position_closed_externally"))
        assert len(events) == 1
