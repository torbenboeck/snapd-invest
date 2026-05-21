"""Portfolio — accounts, positions, P&L.

Pure read/aggregate helpers over `Account`, `Position`, `Trade`, `Bar`.
Mutations to positions happen inside `broker.py` (during fills). Cash mutations
happen inside `broker.py` too.

This module owns the *queries* and the *math*.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from snapd_invest.audit import record_event
from snapd_invest.models import Account, Bar, Instrument, Position, new_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker.saxo import SaxoBroker
    from snapd_invest.clock import Clock


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
    saxo_client_key: str | None = None,
    saxo_account_key: str | None = None,
    saxo_account_id: str | None = None,
) -> Account:
    if account_type not in {"paper", "sim", "live"}:
        raise ValueError(f"account_type must be paper|sim|live, got {account_type!r}")
    if account_type != "sim" and (
        saxo_client_key is not None or saxo_account_key is not None or saxo_account_id is not None
    ):
        raise ValueError("saxo_* fields are only valid for account_type='sim'")
    account = Account(
        id=new_id(),
        name=name,
        account_type=account_type,
        base_currency=base_currency,
        cash=initial_cash,
        created_at=clock.now(),
        saxo_client_key=saxo_client_key,
        saxo_account_key=saxo_account_key,
        saxo_account_id=saxo_account_id,
    )
    session.add(account)
    await session.flush()
    return account


async def get_account_by_name(session: AsyncSession, name: str) -> Account | None:
    stmt = select(Account).where(Account.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_account_by_id(session: AsyncSession, account_id: str) -> Account | None:
    stmt = select(Account).where(Account.id == account_id)
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


async def reconcile_sim_positions(
    session: AsyncSession,
    clock: Clock,
    broker: SaxoBroker,
    account: Account,
) -> None:
    """Reconcile engine `Position` rows against Saxo's view for a sim account.

    Decision matrix per `docs/specs/T-001B-saxo-trading.md` §4.7:

    - **Match** (qty + avg_cost equal): no-op.
    - **Drift** (we have, Saxo has, values differ): update our row to
      Saxo's view; emit `position_drift`.
    - **New** (Saxo has, we don't): create our row tagged `view_only`;
      emit `position_view_only_created`. Also creates the `Instrument`
      row if we don't have one for the UIC yet.
    - **Gone** (we have, Saxo doesn't): zero our quantity; emit
      `position_closed_externally`.

    Saxo is the source of truth for sim accounts — our `Position` rows
    are a cache, not a ledger.
    """
    saxo_positions = await broker.get_positions(session, account=account)

    # Existing engine-side positions for this account.
    existing_positions = list(
        (await session.execute(select(Position).where(Position.account_id == account.id)))
        .scalars()
        .all()
    )

    # Pre-load instruments referenced by either side so we can match by UIC.
    instrument_ids = {p.instrument_id for p in existing_positions}
    existing_instruments_by_id: dict[str, Instrument] = {}
    if instrument_ids:
        rows = (
            (await session.execute(select(Instrument).where(Instrument.id.in_(instrument_ids))))
            .scalars()
            .all()
        )
        existing_instruments_by_id = {i.id: i for i in rows}
    saxo_uics = {p.uic for p in saxo_positions}
    matched_existing_ids: set[str] = set()

    for saxo_pos in saxo_positions:
        instrument = await _instrument_for_saxo_position(session, saxo_pos)
        position = next((p for p in existing_positions if p.instrument_id == instrument.id), None)
        if position is None:
            new_position = Position(
                id=new_id(),
                account_id=account.id,
                instrument_id=instrument.id,
                quantity=saxo_pos.amount,
                avg_cost=saxo_pos.open_price,
                tag="view_only",
                updated_at=clock.now(),
            )
            session.add(new_position)
            await record_event(
                session,
                clock,
                event_type="position_view_only_created",
                payload={
                    "account_id": account.id,
                    "instrument_symbol": instrument.symbol,
                    "instrument_exchange": instrument.exchange,
                    "saxo_uic": saxo_pos.uic,
                    "amount": str(saxo_pos.amount),
                    "open_price": str(saxo_pos.open_price),
                },
            )
            continue
        matched_existing_ids.add(position.id)
        if position.quantity != saxo_pos.amount or position.avg_cost != saxo_pos.open_price:
            await record_event(
                session,
                clock,
                event_type="position_drift",
                payload={
                    "account_id": account.id,
                    "instrument_symbol": instrument.symbol,
                    "saxo_uic": saxo_pos.uic,
                    "engine_quantity": str(position.quantity),
                    "saxo_amount": str(saxo_pos.amount),
                    "engine_avg_cost": str(position.avg_cost),
                    "saxo_open_price": str(saxo_pos.open_price),
                },
            )
            position.quantity = saxo_pos.amount
            position.avg_cost = saxo_pos.open_price
            position.updated_at = clock.now()

    # Anything we had that Saxo no longer reports → zero it out.
    for position in existing_positions:
        if position.id in matched_existing_ids:
            continue
        engine_inst = existing_instruments_by_id.get(position.instrument_id)
        if engine_inst is not None and engine_inst.saxo_uic in saxo_uics:
            # Edge case: matched by UIC even if we missed it above (shouldn't
            # happen given the loop, but be defensive).
            continue
        if position.quantity == Decimal("0"):
            continue
        await record_event(
            session,
            clock,
            event_type="position_closed_externally",
            payload={
                "account_id": account.id,
                "instrument_id": position.instrument_id,
                "previous_quantity": str(position.quantity),
            },
        )
        position.quantity = Decimal("0")
        position.updated_at = clock.now()

    await session.flush()


_FX_BASE_EXCHANGE = "FX"


async def _instrument_for_saxo_position(session: AsyncSession, saxo_pos: object) -> Instrument:
    """Look up an `Instrument` by Saxo UIC, creating it if missing.

    `saxo_pos` is duck-typed to `SaxoPosition`; we keep the runtime type
    string-loose to avoid a `from snapd_invest.broker.saxo import SaxoPosition`
    at module level (which would form a cycle through `broker/__init__.py`'s
    eager PaperBroker import).
    """
    uic = saxo_pos.uic  # type: ignore[attr-defined]
    stmt = select(Instrument).where(Instrument.saxo_uic == uic)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    symbol = saxo_pos.symbol  # type: ignore[attr-defined]
    asset_type = saxo_pos.asset_type  # type: ignore[attr-defined]
    currency = saxo_pos.currency  # type: ignore[attr-defined]
    instrument = Instrument(
        id=new_id(),
        symbol=symbol or f"UIC-{uic}",
        exchange=_FX_BASE_EXCHANGE if asset_type == "FxSpot" else "UNKNOWN",
        instrument_type="fx" if asset_type == "FxSpot" else "stock",
        currency=currency or "DKK",
        tick_size=Decimal("0.00001") if asset_type == "FxSpot" else Decimal("0.01"),
        saxo_uic=uic,
        saxo_asset_type=asset_type,
    )
    session.add(instrument)
    await session.flush()
    return instrument


async def build_summary(
    session: AsyncSession,
    account: Account,
    *,
    broker: SaxoBroker | None = None,
    clock: Clock | None = None,
    _now: datetime | None = None,
) -> PortfolioSummary:
    """Compute the full portfolio summary for an account.

    For `sim` accounts, pass a `SaxoBroker` and `clock` to reconcile our
    `Position` rows against Saxo's view before computing the summary. For
    `paper` accounts the broker arg is ignored.
    """
    _ = _now  # reserved
    if account.account_type == "sim" and broker is not None and clock is not None:
        await reconcile_sim_positions(session, clock, broker, account)
    positions = await list_positions(session, account)

    views: list[PositionView] = []
    equity = account.cash
    any_missing_price = False

    for position in positions:
        instrument = (
            await session.execute(select(Instrument).where(Instrument.id == position.instrument_id))
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
