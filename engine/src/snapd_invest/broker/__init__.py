"""Broker abstraction.

`IBroker` is the contract every execution venue implements:

- `PaperBroker` — in-memory paper-trading (`paper` accounts).
- `SaxoBroker` — Saxo SIM (`sim` accounts). T-001-A: `get_account` only.

This package owns ALL broker imports — neither strategies, agents, the risk
gate, nor execution code should import broker-vendor SDKs directly.

Module ordering: types (IBroker, OrderRequest, OrderResult) are defined
first, PaperBroker is imported last. PaperBroker imports the types from the
parent package via the partial module Python provides during cyclic import
— this keeps the package's public surface in `__init__.py` without forcing
a separate `_types.py` file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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


# ----------------------------------------------------------------------------
# OrderResult — typed discriminated union (ADR-006)
#
# Variants are tagged with a `kind` Literal so call sites can pattern-match
# with `match` + `typing.assert_never` for compiler-enforced exhaustiveness.
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Filled:
    """Order was fully filled."""

    order: Order
    trades: list[Trade] = field(default_factory=list)
    kind: Literal["filled"] = field(default="filled", init=False)


@dataclass(slots=True, frozen=True)
class PartiallyFilled:
    """Order was partially filled; `remaining_quantity` is still working."""

    order: Order
    trades: list[Trade]
    remaining_quantity: Decimal
    kind: Literal["partially_filled"] = field(default="partially_filled", init=False)


@dataclass(slots=True, frozen=True)
class Rejected:
    """Broker rejected the order. `saxo_error_code` is None for non-Saxo brokers."""

    reason: str
    saxo_error_code: str | None = None
    kind: Literal["rejected"] = field(default="rejected", init=False)


@dataclass(slots=True, frozen=True)
class BrokerDown:
    """Broker call failed in a transient way; caller decides retry vs abort."""

    detail: str
    kind: Literal["broker_down"] = field(default="broker_down", init=False)


@dataclass(slots=True, frozen=True)
class IdempotentReplay:
    """Replay of a previously-placed order (same idempotency_key)."""

    order: Order
    trades: list[Trade]
    original_idempotency_key: str
    kind: Literal["idempotent_replay"] = field(default="idempotent_replay", init=False)


OrderResult = Filled | PartiallyFilled | Rejected | BrokerDown | IdempotentReplay


class IBroker(Protocol):
    """Execution venue."""

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> OrderResult: ...
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
    "BrokerDown",
    "BrokerError",
    "BrokerFactory",
    "BrokerHttpError",
    "BrokerTimeoutError",
    "Filled",
    "IBroker",
    "IdempotentReplay",
    "OrderRequest",
    "OrderResult",
    "PaperBroker",
    "PartiallyFilled",
    "Rejected",
    "SaxoAccountInfo",
    "SaxoBroker",
    "Side",
]
