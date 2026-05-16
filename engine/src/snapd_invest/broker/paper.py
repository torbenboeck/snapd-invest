"""PaperBroker — in-memory paper-trading implementation of `IBroker`.

Fills market orders immediately at the latest bar's close price. Limit orders
fill if the limit is "marketable" against the last price (buy: last <= limit;
sell: last >= limit). Otherwise the order is persisted with `rejected` status.

The protocol (`IBroker`) and DTOs (`OrderRequest`, `OrderResult`) live in the
package's `__init__.py` so they can be imported without dragging PaperBroker
internals along.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from snapd_invest.broker import (
    Filled,
    IdempotentReplay,
    OrderRequest,
    OrderResult,
    Rejected,
)
from snapd_invest.models import Bar, Order, Position, Trade, new_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock
    from snapd_invest.models import Instrument


class PaperBroker:
    """In-memory paper broker."""

    venue_name = "paper"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> OrderResult:
        existing = await self._find_by_idempotency_key(session, request.idempotency_key)
        if existing is not None:
            trades_stmt = select(Trade).where(Trade.order_id == existing.id)
            existing_trades = list((await session.execute(trades_stmt)).scalars().all())
            return IdempotentReplay(
                order=existing,
                trades=existing_trades,
                original_idempotency_key=request.idempotency_key,
            )

        last_price = await self.get_last_price(session, instrument=request.instrument)
        if last_price is None:
            await self._persist_order(session, request, status="rejected")
            return Rejected(reason="no last price available", saxo_error_code=None)

        fill_price: Decimal | None = last_price
        if request.limit_price is not None:
            buy_above_limit = request.side == "buy" and last_price > request.limit_price
            sell_below_limit = request.side == "sell" and last_price < request.limit_price
            fill_price = None if buy_above_limit or sell_below_limit else request.limit_price

        if fill_price is None:
            await self._persist_order(session, request, status="rejected")
            return Rejected(reason="limit price not marketable", saxo_error_code=None)

        order = await self._persist_order(session, request, status="filled")
        trade = await self._persist_trade(session, order, fill_price, request.quantity)
        await self._apply_to_position(session, request, fill_price)
        await self._apply_to_cash(session, request, fill_price)

        return Filled(order=order, trades=[trade])

    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None:
        stmt = (
            select(Bar)
            .where(Bar.instrument_id == instrument.id)
            .order_by(Bar.timestamp.desc())
            .limit(1)
        )
        bar = (await session.execute(stmt)).scalar_one_or_none()
        return bar.close if bar else None

    # ----- internals -----

    async def _find_by_idempotency_key(self, session: AsyncSession, key: str) -> Order | None:
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
