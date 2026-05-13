"""Tests for `algo_invest.execution`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.audit import list_events
from algo_invest.broker import PaperBroker
from algo_invest.clock import FakeClock
from algo_invest.data import BarData, ensure_instrument, upsert_bars
from algo_invest.execution import execute_signal
from algo_invest.models import Order, Position
from algo_invest.portfolio import create_account
from algo_invest.risk import RiskConfig
from algo_invest.strategy import Signal


def _setup_signal(account_id: str) -> Signal:
    return Signal(
        source="test_strategy",
        account_id=account_id,
        instrument_symbol="AAPL",
        instrument_exchange="NASDAQ",
        action="buy",
        quantity=Decimal("5"),
        conviction=Decimal("0.7"),
        rationale="test reason",
        emitted_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        correlation_id="corr-1",
    )


async def _seed(
    db_session: AsyncSession, fake_clock: FakeClock, *, last_price: Decimal
) -> tuple[object, object]:
    account = await create_account(
        db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
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
        bars=[
            BarData(
                instrument_symbol="AAPL",
                interval="1d",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                open=last_price,
                high=last_price,
                low=last_price,
                close=last_price,
                volume=Decimal("1000"),
            )
        ],
        source="test",
    )
    return account, instrument


class TestExecuteSignal:
    async def test_happy_path_places_order(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _ = await _seed(db_session, fake_clock, last_price=Decimal("150"))
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id)

        outcome = await execute_signal(
            db_session, fake_clock, broker, RiskConfig(), signal
        )

        assert outcome.gate_allowed
        assert outcome.order_status == "filled"
        # Position created
        position = (
            await db_session.execute(select(Position).where(Position.account_id == account.id))
        ).scalar_one()
        assert position.quantity == Decimal("5")

    async def test_rejected_by_risk_does_not_place_order(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _ = await _seed(db_session, fake_clock, last_price=Decimal("150"))
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id)

        outcome = await execute_signal(
            db_session, fake_clock, broker, RiskConfig(kill_switch=True), signal
        )

        assert not outcome.gate_allowed
        assert outcome.gate_reason == "kill_switch_on"
        orders = list((await db_session.execute(select(Order))).scalars().all())
        assert orders == []

    async def test_audit_events_recorded(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _ = await _seed(db_session, fake_clock, last_price=Decimal("150"))
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id)

        await execute_signal(db_session, fake_clock, broker, RiskConfig(), signal)
        await db_session.commit()

        events = await list_events(db_session, correlation_id="corr-1")
        types = [e.type for e in events]
        assert "signal_emitted" in types
        assert "risk_decision" in types
        assert "order_placed" in types

    async def test_idempotent_replay_uses_same_order(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _ = await _seed(db_session, fake_clock, last_price=Decimal("150"))
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id)

        first = await execute_signal(db_session, fake_clock, broker, RiskConfig(), signal)
        second = await execute_signal(db_session, fake_clock, broker, RiskConfig(), signal)

        assert first.order_id == second.order_id
