"""Tests for `snapd_invest.pipeline`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import structlog

from snapd_invest.agent import CONSERVATIVE_VALUE
from snapd_invest.broker import IBroker, PaperBroker
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.pipeline import (
    expire_overdue_recommendations,
    instrument_type_for_exchange,
    parse_watchlist_entry,
    run_agent_once,
    run_microtrader_once,
)
from snapd_invest.portfolio import create_account
from snapd_invest.promotion import Allowed, PromotionDecision
from snapd_invest.recommendation import create_recommendation
from snapd_invest.risk import RiskConfig
from snapd_invest.strategy import Signal, SMACrossoverConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account, Instrument


def _factory_for(broker: PaperBroker) -> BrokerFactory:
    def factory(_account: Account) -> IBroker:
        return broker

    return factory


def _allow_all(_account: Account, _broker: IBroker) -> PromotionDecision:
    return Allowed()


class TestParseWatchlistEntry:
    def test_valid_entry(self) -> None:
        symbol, exchange = parse_watchlist_entry("AAPL@NASDAQ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_dashes_in_symbol(self) -> None:
        symbol, exchange = parse_watchlist_entry("BTC-USD@BINANCE")
        assert symbol == "BTC-USD"
        assert exchange == "BINANCE"

    def test_strips_whitespace(self) -> None:
        symbol, exchange = parse_watchlist_entry("  AAPL @ NASDAQ  ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_missing_at_sign(self) -> None:
        with pytest.raises(ValueError, match="SYMBOL@EXCHANGE"):
            parse_watchlist_entry("AAPL")

    def test_empty_symbol(self) -> None:
        with pytest.raises(ValueError, match="empty symbol"):
            parse_watchlist_entry("@NASDAQ")

    def test_empty_exchange(self) -> None:
        with pytest.raises(ValueError, match="empty exchange"):
            parse_watchlist_entry("AAPL@")


class TestInstrumentTypeForExchange:
    def test_fx_exchange_returns_fx(self) -> None:
        assert instrument_type_for_exchange("FX") == "fx"

    def test_fx_is_case_insensitive(self) -> None:
        assert instrument_type_for_exchange("fx") == "fx"
        assert instrument_type_for_exchange(" fx ") == "fx"

    def test_anything_else_returns_stock(self) -> None:
        assert instrument_type_for_exchange("NASDAQ") == "stock"
        assert instrument_type_for_exchange("NYSE") == "stock"
        assert instrument_type_for_exchange("OMXC25") == "stock"


async def _seed_aapl_with_golden_cross_bars(session: AsyncSession) -> Instrument:
    """Ensure an AAPL instrument with bars that produce a golden cross at
    the last index for short_period=2, long_period=5."""
    instrument = await ensure_instrument(
        session,
        symbol="AAPL",
        exchange="NASDAQ",
        instrument_type="stock",
        currency="USD",
    )
    closes = (
        [Decimal("100")] * 10
        + [Decimal("90")] * 5
        + [Decimal("80"), Decimal("80"), Decimal("80")]
        + [Decimal("200")]
    )
    bars = [
        BarData(
            instrument_symbol="AAPL",
            interval="1d",
            timestamp=datetime(2026, 4, 1, tzinfo=UTC).replace(day=i + 1),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=Decimal("1000"),
        )
        for i, c in enumerate(closes)
    ]
    await upsert_bars(session, instrument=instrument, bars=bars, source="test")
    return instrument


class TestRunMicroTraderOnce:
    async def test_emits_buy_signal_on_golden_cross(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
        )
        instrument = await _seed_aapl_with_golden_cross_bars(db_session)
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert len(outcome.signals) == 1
        assert outcome.signals[0].action == "buy"
        assert len(outcome.execution_summaries) == 1
        assert outcome.execution_summaries[0]["gate_allowed"] is True

    async def test_no_signal_when_no_bars(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert outcome.signals == []
        assert outcome.execution_summaries == []

    async def test_binds_correlation_id_to_structlog_contextvars(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """C-14: scheduled ticks have no HTTP middleware to bind correlation_id.
        The pipeline must bind it itself so inner logs carry the ID.

        We assert this by intercepting the BrokerFactory: when called during
        the tick, the current contextvars dict must contain the correlation_id.
        """
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
        )
        instrument = await _seed_aapl_with_golden_cross_bars(db_session)
        broker = PaperBroker(fake_clock)
        observed: dict[str, str] = {}

        def factory(_account: Account) -> IBroker:
            observed.update({k: str(v) for k, v in structlog.contextvars.get_contextvars().items()})
            return broker

        # Pre-condition: no correlation_id bound on entry.
        assert "correlation_id" not in structlog.contextvars.get_contextvars()

        await run_microtrader_once(
            db_session,
            fake_clock,
            factory,
            _allow_all,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
            correlation_id="corr-xyz",
        )

        assert observed.get("correlation_id") == "corr-xyz"

        # Post-condition: helper unbound on exit.
        assert "correlation_id" not in structlog.contextvars.get_contextvars()

    async def test_risk_kill_switch_rejects_signal(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """Proves the risk gate is wired into the pipeline by exercising the
        kill_switch path. Cash and position-size checks are covered directly
        in `test_risk.py` and `test_execution.py`.
        """
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
        )
        instrument = await _seed_aapl_with_golden_cross_bars(db_session)
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(kill_switch=True),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert len(outcome.signals) == 1
        assert outcome.execution_summaries[0]["gate_allowed"] is False
        assert outcome.execution_summaries[0]["gate_reason"] == "kill_switch_on"


class TestRunAgentOnce:
    async def test_creates_recommendation_when_agent_emits_signal(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "AAPL still undervalued.",
                "signals": [
                    {
                        "instrument_symbol": "AAPL",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 5,
                        "conviction": 0.8,
                        "rationale": "below intrinsic value",
                    }
                ],
            }
        )

        outcome = await run_agent_once(
            db_session,
            fake_clock,
            llm,
            account=account,
            instrument=instrument,
            personality=CONSERVATIVE_VALUE,
        )

        assert outcome.recommendation_id is not None
        assert outcome.agent_name == CONSERVATIVE_VALUE.name
        assert "undervalued" in outcome.summary

    async def test_no_recommendation_when_agent_emits_nothing(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        llm = FakeLlmProvider()
        llm.enqueue_json({"summary": "Skipping — too volatile.", "signals": []})

        outcome = await run_agent_once(
            db_session,
            fake_clock,
            llm,
            account=account,
            instrument=instrument,
            personality=CONSERVATIVE_VALUE,
        )

        assert outcome.recommendation_id is None
        assert "volatile" in outcome.summary


class TestExpireOverdueRecommendations:
    async def test_expires_overdue_leaves_fresh(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        signal = Signal(
            source="test",
            account_id=account.id,
            instrument_symbol=instrument.symbol,
            instrument_exchange=instrument.exchange,
            action="buy",
            quantity=Decimal("1"),
            conviction=Decimal("0.8"),
            rationale="test",
            emitted_at=fake_clock.now(),
            correlation_id=None,
        )
        short = await create_recommendation(
            db_session,
            fake_clock,
            agent_id="agent-1",
            signals=[signal],
            rationale="short",
            ttl=timedelta(minutes=1),
        )
        fresh = await create_recommendation(
            db_session,
            fake_clock,
            agent_id="agent-1",
            signals=[signal],
            rationale="fresh",
        )

        fake_clock.advance(hours=2)

        expired_count = await expire_overdue_recommendations(db_session, fake_clock)

        assert expired_count == 1
        await db_session.refresh(short)
        await db_session.refresh(fresh)
        assert short.status == "expired"
        assert fresh.status == "pending"
