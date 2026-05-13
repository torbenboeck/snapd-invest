"""Tests for `snapd_invest.risk`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.clock import FakeClock
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.portfolio import create_account
from snapd_invest.risk import RiskConfig, SignalCandidate, evaluate


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
    async def test_allows_normal_buy(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
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
                limit_price=Decimal("100"),
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
                limit_price=Decimal("1"),
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
                limit_price=Decimal("1"),
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
                limit_price=Decimal("1"),
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
                limit_price=Decimal("100"),
            ),
        )
        assert decision.reason is not None
        assert "position_too_large" in decision.reason

    async def test_insufficient_cash(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
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
                limit_price=Decimal("100"),
            ),
        )
        assert decision.reason is not None
        assert "insufficient_cash" in decision.reason
