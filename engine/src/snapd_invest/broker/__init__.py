"""Broker abstraction.

`IBroker` is the contract every execution venue implements:

- `PaperBroker` — in-memory paper-trading (`paper` accounts).
- `SaxoBroker` — Saxo SIM (`sim` accounts). T-001-A: `get_account` only.

This package owns ALL broker imports — neither strategies, agents, the risk
gate, nor execution code should import broker-vendor SDKs directly.

Module ordering: types (IBroker, OrderRequest, FillResult) are defined first,
PaperBroker is imported last. PaperBroker imports the types from the parent
package via the partial module Python provides during cyclic import — this
keeps the package's public surface in `__init__.py` without forcing a
separate `_types.py` file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.models import Account, Instrument, Order, Trade

Side = Literal["buy", "sell"]


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class BrokerError(Exception):
    """Base class for all broker-layer failures."""


class BrokerAuthError(BrokerError):
    """OAuth token problem — missing, expired, or refresh failed."""


class BrokerHttpError(BrokerError):
    """Broker returned an HTTP error (4xx / 5xx)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"broker HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


class BrokerTimeoutError(BrokerError):
    """Broker call timed out."""


# ----------------------------------------------------------------------------
# Protocol + DTOs
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class OrderRequest:
    """Request to place an order. Validated by `risk.py` before reaching here."""

    account: Account
    instrument: Instrument
    side: Side
    quantity: Decimal
    limit_price: Decimal | None
    source: str
    idempotency_key: str
    correlation_id: str | None = None


@dataclass(slots=True, frozen=True)
class FillResult:
    """Outcome of placing an order."""

    order: Order
    trades: list[Trade]
    was_idempotent_replay: bool


class IBroker(Protocol):
    """Execution venue."""

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> FillResult: ...
    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None: ...


# Reserved for T-001-B when SaxoBroker starts placing orders. T-001-A only
# uses it from the new `GET /v1/accounts/{id}` route, where the account_type
# determines which broker class handles the call.
BrokerFactory = Callable[["Account"], IBroker]


# Imported last so the types above are already attributes of this package
# when PaperBroker / SaxoBroker run their top-level
# `from snapd_invest.broker import ...`.
from snapd_invest.broker.paper import PaperBroker  # noqa: E402
from snapd_invest.broker.saxo import SaxoAccountInfo, SaxoBroker  # noqa: E402

__all__ = [
    "BrokerAuthError",
    "BrokerError",
    "BrokerFactory",
    "BrokerHttpError",
    "BrokerTimeoutError",
    "FillResult",
    "IBroker",
    "OrderRequest",
    "PaperBroker",
    "SaxoAccountInfo",
    "SaxoBroker",
    "Side",
]
