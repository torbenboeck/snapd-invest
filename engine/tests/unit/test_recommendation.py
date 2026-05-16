"""Tests for `snapd_invest.recommendation`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from snapd_invest.agent import ensure_default_agent
from snapd_invest.broker import IBroker, PaperBroker
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.portfolio import create_account
from snapd_invest.promotion import Allowed, PromotionDecision
from snapd_invest.recommendation import (
    SignalModification,
    approve_and_execute,
    create_recommendation,
    expire_overdue,
    get_recommendation,
    list_recommendations,
    reject,
)
from snapd_invest.risk import RiskConfig
from snapd_invest.strategy import Signal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account


def _factory_for(broker: PaperBroker) -> BrokerFactory:
    def factory(_account: Account) -> IBroker:
        return broker

    return factory


def _allow_all(_account: Account, _broker: IBroker) -> PromotionDecision:
    return Allowed()


async def _setup_world(session: AsyncSession, clock: FakeClock) -> tuple[object, object, object]:
    account = await create_account(session, clock, name="paper", initial_cash=Decimal("100000"))
    instrument = await ensure_instrument(
        session, symbol="AAPL", exchange="NASDAQ", instrument_type="stock", currency="USD"
    )
    await upsert_bars(
        session,
        instrument=instrument,
        bars=[
            BarData(
                instrument_symbol="AAPL",
                interval="1d",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1000"),
            )
        ],
        source="t",
    )
    agent = await ensure_default_agent(session, clock, account=account)
    return account, instrument, agent


def _signal(account_id: str, quantity: Decimal = Decimal("5")) -> Signal:
    return Signal(
        source="agent:test",
        account_id=account_id,
        instrument_symbol="AAPL",
        instrument_exchange="NASDAQ",
        action="buy",
        quantity=quantity,
        conviction=Decimal("0.8"),
        rationale="test",
        emitted_at=datetime(2026, 5, 1, tzinfo=UTC),
        correlation_id="corr-1",
        reference_price=Decimal("100"),
    )


class TestCreateRecommendation:
    async def test_persists_pending(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="test",
        )
        assert rec.status == "pending"
        assert rec.expires_at > rec.created_at

    async def test_rejects_empty(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        _, _, agent = await _setup_world(db_session, fake_clock)
        with pytest.raises(ValueError, match="at least one signal"):
            await create_recommendation(
                db_session, fake_clock, agent_id=agent.id, signals=[], rationale="t"
            )


class TestListRecommendations:
    async def test_filters_by_status(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
        )
        await reject(db_session, fake_clock, recommendation=rec)

        rec2 = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t2",
        )
        _ = rec2

        pending = await list_recommendations(db_session, status="pending")
        rejected = await list_recommendations(db_session, status="rejected")
        assert len(pending) == 1
        assert len(rejected) == 1


class TestApproveAndExecute:
    async def test_executes_signals_through_pipeline(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id, quantity=Decimal("3"))],
            rationale="test",
        )
        broker = PaperBroker(fake_clock)

        outcome = await approve_and_execute(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(),
            recommendation=rec,
        )
        assert rec.status == "executed"
        assert len(outcome.execution_summaries) == 1
        assert outcome.execution_summaries[0]["order_status"] == "filled"

    async def test_modifications_change_quantity(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id, quantity=Decimal("10"))],
            rationale="t",
        )
        broker = PaperBroker(fake_clock)

        outcome = await approve_and_execute(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(),
            recommendation=rec,
            modifications=[
                SignalModification(
                    instrument_symbol="AAPL",
                    instrument_exchange="NASDAQ",
                    quantity=Decimal("2"),
                )
            ],
        )
        assert outcome.execution_summaries[0]["quantity"] == "2"

    async def test_skip_modification_excludes_signal(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
        )
        broker = PaperBroker(fake_clock)
        outcome = await approve_and_execute(
            db_session,
            fake_clock,
            _factory_for(broker),
            _allow_all,
            RiskConfig(),
            recommendation=rec,
            modifications=[
                SignalModification(
                    instrument_symbol="AAPL",
                    instrument_exchange="NASDAQ",
                    skip=True,
                )
            ],
        )
        assert outcome.execution_summaries == []

    async def test_rejects_non_pending(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
        )
        broker = PaperBroker(fake_clock)
        factory = _factory_for(broker)
        await approve_and_execute(
            db_session, fake_clock, factory, _allow_all, RiskConfig(), recommendation=rec
        )
        # Try again
        with pytest.raises(ValueError, match="not pending"):
            await approve_and_execute(
                db_session, fake_clock, factory, _allow_all, RiskConfig(), recommendation=rec
            )

    async def test_rejects_expired(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
            ttl=timedelta(minutes=1),
        )
        fake_clock.advance(hours=2)
        broker = PaperBroker(fake_clock)

        with pytest.raises(ValueError, match="expired"):
            await approve_and_execute(
                db_session,
                fake_clock,
                _factory_for(broker),
                _allow_all,
                RiskConfig(),
                recommendation=rec,
            )


class TestReject:
    async def test_marks_rejected(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
        )
        await reject(db_session, fake_clock, recommendation=rec, reason="not now")
        assert rec.status == "rejected"


class TestExpireOverdue:
    async def test_expires_past_deadline(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
            ttl=timedelta(minutes=10),
        )
        fake_clock.advance(hours=1)
        expired_count = await expire_overdue(db_session, fake_clock)
        assert expired_count == 1

    async def test_leaves_unexpired_alone(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, _, agent = await _setup_world(db_session, fake_clock)
        rec = await create_recommendation(
            db_session,
            fake_clock,
            agent_id=agent.id,
            signals=[_signal(account.id)],
            rationale="t",
            ttl=timedelta(hours=24),
        )
        await expire_overdue(db_session, fake_clock)
        fresh = await get_recommendation(db_session, rec.id)
        assert fresh is not None
        assert fresh.status == "pending"
