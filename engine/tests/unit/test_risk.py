"""Tests for `snapd_invest.risk`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete

from snapd_invest.broker import OrderRequest
from snapd_invest.broker.paper import PaperBroker
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.models import Bar
from snapd_invest.portfolio import create_account
from snapd_invest.risk import RiskConfig, SignalCandidate, evaluate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account, Instrument


def _bar(symbol: str, close: Decimal) -> BarData:
    return BarData(
        instrument_symbol=symbol,
        interval="1d",
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
    )


class TestRiskGate:
    async def test_allows_normal_buy(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
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
            db_session, instrument=instrument, bars=[_bar("AAPL", Decimal("100"))], source="t"
        )

        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                reference_price=Decimal("100"),
            ),
        )
        assert decision.allowed

    async def test_kill_switch_blocks_everything(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="X", exchange="Y", instrument_type="stock", currency="USD"
        )
        config = RiskConfig(kill_switch=True)
        decision = await evaluate(
            db_session,
            config,
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("1"),
                reference_price=Decimal("1"),
            ),
        )
        assert not decision.allowed
        assert decision.reason == "kill_switch_on"

    async def test_rejects_non_positive_quantity(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="X", exchange="Y", instrument_type="stock", currency="USD"
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("0"),
                reference_price=Decimal("1"),
            ),
        )
        assert decision.reason == "non_positive_quantity"

    async def test_allowlist_blocks_others(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="MSFT",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        config = RiskConfig(instrument_allowlist=frozenset({"AAPL@NASDAQ"}))
        decision = await evaluate(
            db_session,
            config,
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("1"),
                reference_price=Decimal("1"),
            ),
        )
        assert decision.reason is not None
        assert "instrument_not_allowed" in decision.reason

    async def test_position_size_too_large(
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
            db_session, instrument=instrument, bars=[_bar("AAPL", Decimal("100"))], source="t"
        )
        # 30% of 10000 = 3000, but 50 * 100 = 5000
        config = RiskConfig(max_position_pct_of_equity=Decimal("0.30"))
        decision = await evaluate(
            db_session,
            config,
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("50"),
                reference_price=Decimal("100"),
            ),
        )
        assert decision.reason is not None
        assert "position_too_large" in decision.reason

    async def test_buy_without_reference_price_rejected(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """A buy with no reference_price cannot be sized — the gate fails safe
        rather than silently approve an unbounded position.
        """
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
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                reference_price=None,
            ),
        )
        assert decision.reason == "missing_reference_price"

    async def test_sell_without_reference_price_allowed(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """Sells reduce exposure; no reference price needed for the cash check."""
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("1000")
        )
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=instrument,
            quantity=Decimal("5"),
            avg_cost=Decimal("100"),
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("1"),
                reference_price=None,
            ),
        )
        assert decision.allowed

    async def test_insufficient_cash(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
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
            db_session, instrument=instrument, bars=[_bar("AAPL", Decimal("100"))], source="t"
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("10"),
                reference_price=Decimal("100"),
            ),
        )
        assert decision.reason is not None
        assert "insufficient_cash" in decision.reason


async def _seed_position(
    session: AsyncSession,
    clock: FakeClock,
    *,
    account: Account,
    instrument: Instrument,
    quantity: Decimal,
    avg_cost: Decimal,
) -> None:
    """Buy `quantity` shares at `avg_cost` via the PaperBroker to set up a position."""
    await upsert_bars(
        session, instrument=instrument, bars=[_bar(instrument.symbol, avg_cost)], source="t"
    )
    broker = PaperBroker(clock)
    await broker.place_order(
        session,
        OrderRequest(
            account=account,
            instrument=instrument,
            side="buy",
            quantity=quantity,
            limit_price=None,
            idempotency_key=f"seed-{instrument.symbol}-{quantity}",
            source="test-seed",
            correlation_id=None,
        ),
    )


class TestRiskSellGuard:
    """C-01: sells must not exceed held quantity (no shorting per mvp-scope)."""

    async def test_rejects_sell_when_no_position(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("5"),
                reference_price=None,
            ),
        )
        assert decision.reason is not None
        assert "insufficient_position" in decision.reason

    async def test_rejects_oversell_when_position_short(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=instrument,
            quantity=Decimal("10"),
            avg_cost=Decimal("100"),
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("11"),
                reference_price=None,
            ),
        )
        assert decision.reason is not None
        assert "insufficient_position" in decision.reason

    async def test_allows_sell_exactly_held(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=instrument,
            quantity=Decimal("10"),
            avg_cost=Decimal("100"),
        )
        decision = await evaluate(
            db_session,
            RiskConfig(),
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("10"),
                reference_price=None,
            ),
        )
        assert decision.allowed


class TestRiskEquityFallback:
    """C-09: when equity is unknown the gate falls back to cash; warn operator."""

    async def test_warns_when_equity_unknown(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        held_instrument = await ensure_instrument(
            db_session, symbol="MSFT", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        # Seed a position at avg_cost=100, then *remove* the most recent bar so
        # the portfolio summary cannot price it — equity becomes None.
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=held_instrument,
            quantity=Decimal("5"),
            avg_cost=Decimal("100"),
        )
        await db_session.execute(delete(Bar).where(Bar.instrument_id == held_instrument.id))
        await db_session.flush()

        buy_instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )

        with structlog.testing.capture_logs() as logs:
            decision = await evaluate(
                db_session,
                RiskConfig(),
                SignalCandidate(
                    account=account,
                    instrument=buy_instrument,
                    side="buy",
                    quantity=Decimal("1"),
                    reference_price=Decimal("100"),
                ),
            )
        assert decision.allowed
        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert any("risk_equity_unknown_using_cash_proxy" in e.get("event", "") for e in warnings)


class TestRiskDailyLossCircuitBreaker:
    """C-10: max_daily_loss_pct halts trading when realized losses exceed the threshold."""

    async def test_rejects_when_daily_realized_loss_exceeds_pct(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10000")
        )
        instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=instrument,
            quantity=Decimal("10"),
            avg_cost=Decimal("200"),
        )
        # Sell at half the cost → realized loss = (100-200)*10 = -1000.
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[
                BarData(
                    instrument_symbol="AAPL",
                    interval="1d",
                    timestamp=datetime(2026, 5, 2, tzinfo=UTC),
                    open=Decimal("100"),
                    high=Decimal("100"),
                    low=Decimal("100"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                )
            ],
            source="t",
        )
        broker = PaperBroker(fake_clock)
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("10"),
                limit_price=None,
                idempotency_key="loss-trade",
                source="test",
                correlation_id=None,
            ),
        )
        # equity after sell ≈ cash 9000; 5% of 9000 = 450; loss 1000 > 450.
        config = RiskConfig(max_daily_loss_pct=Decimal("0.05"))
        decision = await evaluate(
            db_session,
            config,
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("1"),
                reference_price=Decimal("100"),
            ),
            clock=fake_clock,
        )
        assert decision.reason is not None
        assert "daily_loss_exceeded" in decision.reason

    async def test_allows_when_daily_realized_loss_within_pct(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
        )
        instrument = await ensure_instrument(
            db_session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
        )
        await _seed_position(
            db_session,
            fake_clock,
            account=account,
            instrument=instrument,
            quantity=Decimal("10"),
            avg_cost=Decimal("200"),
        )
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[
                BarData(
                    instrument_symbol="AAPL",
                    interval="1d",
                    timestamp=datetime(2026, 5, 2, tzinfo=UTC),
                    open=Decimal("195"),
                    high=Decimal("195"),
                    low=Decimal("195"),
                    close=Decimal("195"),
                    volume=Decimal("1"),
                )
            ],
            source="t",
        )
        broker = PaperBroker(fake_clock)
        await broker.place_order(
            db_session,
            OrderRequest(
                account=account,
                instrument=instrument,
                side="sell",
                quantity=Decimal("10"),
                limit_price=None,
                idempotency_key="small-loss",
                source="test",
                correlation_id=None,
            ),
        )
        # Loss = (195-200)*10 = -50; equity ≈ 99950; 5% threshold = ~5000. Allowed.
        config = RiskConfig(max_daily_loss_pct=Decimal("0.05"))
        decision = await evaluate(
            db_session,
            config,
            SignalCandidate(
                account=account,
                instrument=instrument,
                side="buy",
                quantity=Decimal("1"),
                reference_price=Decimal("100"),
            ),
            clock=fake_clock,
        )
        assert decision.allowed
