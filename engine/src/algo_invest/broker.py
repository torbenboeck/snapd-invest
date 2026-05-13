"""Broker abstraction and the internal PaperBroker.

`IBroker` is the contract every execution venue implements. PaperBroker is the
in-memory paper-trading implementation used at MVP and in tests. SaxoBroker
arrives in a later PR.

Design rules:
- Brokers know nothing about strategies, agents, or recommendations.
- Brokers receive `OrderRequest` value objects and return persisted `Order` +
  resulting `Trade` rows.
- Idempotency: brokers refuse duplicate `idempotency_key` values silently
  (return the existing Order).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.clock import Clock
from algo_invest.models import Account, Instrument, Order, Position, Trade, new_id

Side = Literal["buy", "sell"]


@dataclass(slots=True, frozen=True)
class OrderRequest:
    """Request to place an order. Validated by `risk.py` before reaching here."""

    account: Account
    instrument: Instrument
    side: Side
    quantity: Decimal
    limit_price: Decimal | None
    source: str  # which strategy or agent originated this
    idempotency_key: str
    correlation_id: str | None = None


@dataclass(slots=True, frozen=True)
class FillResult:
    """Outcome of placing an order."""

    order: Order
    trades: list[Trade]
    was_idempotent_replay: bool


class IBroker(Protocol):
    """Execution venue. Implementations: PaperBroker, SaxoBroker (later)."""

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> FillResult:
        """Submit an order. Implementations decide fill semantics."""
        ...

    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None:
        """Best-effort last known price for the instrument. Used by paper fills."""
        ...


# ----------------------------------------------------------------------------
# PaperBroker
# ----------------------------------------------------------------------------


class PaperBroker:
    """In-memory paper broker.

    Fills market orders immediately at the latest bar's close price.
    Limit orders fill if the limit is "marketable" against the last price
    (buy: last <= limit; sell: last >= limit). Otherwise rejected with a
    `rejected` status.
    """

    venue_name = "paper"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> FillResult:
        # Idempotency check
        existing = await self._find_by_idempotency_key(session, request.idempotency_key)
        if existing is not None:
            trades_stmt = select(Trade).where(Trade.order_id == existing.id)
            existing_trades = list((await session.execute(trades_stmt)).scalars().all())
            return FillResult(order=existing, trades=existing_trades, was_idempotent_replay=True)

        last_price = await self.get_last_price(session, instrument=request.instrument)
        if last_price is None:
            order = await self._persist_order(session, request, status="rejected")
            return FillResult(order=order, trades=[], was_idempotent_replay=False)

        # Determine fill price
        fill_price: Decimal | None = last_price
        if request.limit_price is not None:
            if request.side == "buy" and last_price > request.limit_price:
                fill_price = None
            elif request.side == "sell" and last_price < request.limit_price:
                fill_price = None
            else:
                fill_price = request.limit_price

        if fill_price is None:
            order = await self._persist_order(session, request, status="rejected")
            return FillResult(order=order, trades=[], was_idempotent_replay=False)

        order = await self._persist_order(session, request, status="filled")
        trade = await self._persist_trade(session, order, fill_price, request.quantity)
        await self._apply_to_position(session, request, fill_price)
        await self._apply_to_cash(session, request, fill_price)

        return FillResult(order=order, trades=[trade], was_idempotent_replay=False)

    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None:
        from algo_invest.models import Bar

        stmt = (
            select(Bar)
            .where(Bar.instrument_id == instrument.id)
            .order_by(Bar.timestamp.desc())
            .limit(1)
        )
        bar = (await session.execute(stmt)).scalar_one_or_none()
        return bar.close if bar else None

    # ----- internals -----

    async def _find_by_idempotency_key(
        self, session: AsyncSession, key: str
    ) -> Order | None:
        stmt = select(Order).where(Order.idempotency_key == key)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _persist_order(
        self,
        session: AsyncSession,
        request: OrderRequest,
        *,
        status: str,
    ) -> Order:
        order = Order(
            id=new_id(),
            account_id=request.account.id,
            instrument_id=request.instrument.id,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            status=status,
            idempotency_key=request.idempotency_key,
            source=request.source,
            correlation_id=request.correlation_id,
            submitted_at=self._clock.now(),
        )
        session.add(order)
        await session.flush()
        return order

    async def _persist_trade(
        self,
        session: AsyncSession,
        order: Order,
        fill_price: Decimal,
        fill_quantity: Decimal,
    ) -> Trade:
        trade = Trade(
            id=new_id(),
            order_id=order.id,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            fees=Decimal("0"),
            venue=self.venue_name,
            occurred_at=self._clock.now(),
        )
        session.add(trade)
        await session.flush()
        return trade

    async def _apply_to_position(
        self,
        session: AsyncSession,
        request: OrderRequest,
        fill_price: Decimal,
    ) -> None:
        stmt = select(Position).where(
            Position.account_id == request.account.id,
            Position.instrument_id == request.instrument.id,
        )
        position = (await session.execute(stmt)).scalar_one_or_none()
        delta = request.quantity if request.side == "buy" else -request.quantity

        if position is None:
            position = Position(
                id=new_id(),
                account_id=request.account.id,
                instrument_id=request.instrument.id,
                quantity=delta,
                avg_cost=fill_price if request.side == "buy" else Decimal("0"),
                tag="managed",
                updated_at=self._clock.now(),
            )
            session.add(position)
        else:
            new_qty = position.quantity + delta
            if request.side == "buy" and new_qty > 0:
                # weighted average cost
                total_cost = (position.quantity * position.avg_cost) + (
                    request.quantity * fill_price
                )
                position.avg_cost = total_cost / new_qty
            position.quantity = new_qty
            position.updated_at = self._clock.now()
        await session.flush()

    async def _apply_to_cash(
        self,
        session: AsyncSession,
        request: OrderRequest,
        fill_price: Decimal,
    ) -> None:
        amount = request.quantity * fill_price
        if request.side == "buy":
            request.account.cash -= amount
        else:
            request.account.cash += amount
        await session.flush()
