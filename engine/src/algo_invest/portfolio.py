"""Portfolio — accounts, positions, P&L.

Pure read/aggregate helpers over `Account`, `Position`, `Trade`, `Bar`.
Mutations to positions happen inside `broker.py` (during fills). Cash mutations
happen inside `broker.py` too.

This module owns the *queries* and the *math*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.clock import Clock
from algo_invest.models import Account, Bar, Instrument, Position, new_id


@dataclass(slots=True, frozen=True)
class PositionView:
    """A position enriched with current price + market value + unrealized P&L."""

    account_id: str
    instrument_symbol: str
    instrument_exchange: str
    quantity: Decimal
    avg_cost: Decimal
    last_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    tag: str


@dataclass(slots=True, frozen=True)
class PortfolioSummary:
    """Top-level view: cash, equity (cash + Σ market_value), positions."""

    account_id: str
    account_name: str
    base_currency: str
    cash: Decimal
    equity: Decimal | None
    positions: list[PositionView]


async def create_account(
    session: AsyncSession,
    clock: Clock,
    *,
    name: str,
    account_type: str = "paper",
    base_currency: str = "DKK",
    initial_cash: Decimal = Decimal("0"),
) -> Account:
    if account_type not in {"paper", "sim", "live"}:
        raise ValueError(f"account_type must be paper|sim|live, got {account_type!r}")
    account = Account(
        id=new_id(),
        name=name,
        account_type=account_type,
        base_currency=base_currency,
        cash=initial_cash,
        created_at=clock.now(),
    )
    session.add(account)
    await session.flush()
    return account


async def get_account_by_name(session: AsyncSession, name: str) -> Account | None:
    stmt = select(Account).where(Account.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_positions(session: AsyncSession, account: Account) -> Sequence[Position]:
    stmt = select(Position).where(
        Position.account_id == account.id, Position.quantity != Decimal("0")
    )
    return (await session.execute(stmt)).scalars().all()


async def _last_price(session: AsyncSession, instrument: Instrument) -> Decimal | None:
    stmt = (
        select(Bar)
        .where(Bar.instrument_id == instrument.id)
        .order_by(Bar.timestamp.desc())
        .limit(1)
    )
    bar = (await session.execute(stmt)).scalar_one_or_none()
    return bar.close if bar else None


async def build_summary(
    session: AsyncSession, account: Account, *, _now: datetime | None = None
) -> PortfolioSummary:
    """Compute the full portfolio summary for an account."""
    _ = _now  # reserved
    positions = await list_positions(session, account)

    views: list[PositionView] = []
    equity = account.cash
    any_missing_price = False

    for position in positions:
        instrument = (
            await session.execute(
                select(Instrument).where(Instrument.id == position.instrument_id)
            )
        ).scalar_one()
        last = await _last_price(session, instrument)
        if last is None:
            any_missing_price = True
            market_value = None
            unrealized = None
        else:
            market_value = (position.quantity * last).quantize(Decimal("0.0001"))
            unrealized = ((last - position.avg_cost) * position.quantity).quantize(
                Decimal("0.0001")
            )
            equity = equity + market_value
        views.append(
            PositionView(
                account_id=account.id,
                instrument_symbol=instrument.symbol,
                instrument_exchange=instrument.exchange,
                quantity=position.quantity,
                avg_cost=position.avg_cost,
                last_price=last,
                market_value=market_value,
                unrealized_pnl=unrealized,
                tag=position.tag,
            )
        )

    return PortfolioSummary(
        account_id=account.id,
        account_name=account.name,
        base_currency=account.base_currency,
        cash=account.cash,
        equity=None if any_missing_price else equity,
        positions=views,
    )
