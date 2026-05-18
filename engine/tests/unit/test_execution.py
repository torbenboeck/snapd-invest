"""Tests for `snapd_invest.execution`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from snapd_invest.audit import list_events
from snapd_invest.broker import PaperBroker
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.execution import execute_signal
from snapd_invest.models import Order, Position
from snapd_invest.portfolio import create_account
from snapd_invest.promotion import Allowed
from snapd_invest.risk import RiskConfig
from snapd_invest.strategy import Signal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


def _setup_signal(account_id: str, *, reference_price: Decimal | None = Decimal("150")) -> Signal:
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
        reference_price=reference_price,
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
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
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
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(kill_switch=True),
            signal,
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

        await execute_signal(
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
        )
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

        first = await execute_signal(
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
        )
        second = await execute_signal(
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
        )

        assert first.order_id == second.order_id

    async def test_market_buy_with_insufficient_cash_is_rejected(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """A buy that would cost more than the account holds must be rejected
        even when the broker leg is a market order (limit_price=None). The
        risk gate uses `Signal.reference_price` for valuation; the order
        itself stays a market order.
        """
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100")
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
                    open=Decimal("150"),
                    high=Decimal("150"),
                    low=Decimal("150"),
                    close=Decimal("150"),
                    volume=Decimal("1000"),
                )
            ],
            source="test",
        )
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id)  # qty=5, reference_price=150 -> cost 750

        outcome = await execute_signal(
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
        )

        assert not outcome.gate_allowed
        assert outcome.gate_reason is not None
        assert "insufficient_cash" in outcome.gate_reason
        assert outcome.order_id is None

    async def test_buy_without_reference_price_is_rejected(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """A buy with no reference_price cannot be sized — the gate must
        fail safe rather than silently approve."""
        account, _ = await _seed(db_session, fake_clock, last_price=Decimal("150"))
        broker = PaperBroker(fake_clock)
        signal = _setup_signal(account.id, reference_price=None)

        outcome = await execute_signal(
            db_session,
            fake_clock,
            lambda _account: broker,
            lambda _account, _broker: Allowed(),
            RiskConfig(),
            signal,
        )

        assert not outcome.gate_allowed
        assert outcome.gate_reason == "missing_reference_price"
