"""SQLAlchemy ORM models.

Single source of truth for the database schema. Alembic autogenerates
migrations from these models.

Conventions:
- All primary keys are `String` UUIDs (generated at the boundary).
- All timestamps are timezone-aware UTC (`DateTime(timezone=True)`).
- All monetary values use `Numeric(precision=18, scale=4)`.
- All quantities use `Numeric(precision=18, scale=8)` (crypto needs precision).
- All payloads of unstructured shape are stored as JSON-serialized text.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 - SQLAlchemy resolves Mapped[datetime] at runtime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from snapd_invest.persistence import Base

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def new_id() -> str:
    """Generate a new UUID string. Use at the boundary, not inside business logic."""
    return str(uuid.uuid4())


_PRICE = Numeric(precision=18, scale=4)
_QTY = Numeric(precision=18, scale=8)

# ----------------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------------


class AuditEvent(Base):
    """Immutable audit record. Append-only."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (Index("ix_audit_events_type_occurred_at", "type", "occurred_at"),)


# ----------------------------------------------------------------------------
# Trading primitives
# ----------------------------------------------------------------------------


class Instrument(Base):
    """A tradable financial asset."""

    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(_PRICE, nullable=False, default=Decimal("0.01"))

    __table_args__ = (
        UniqueConstraint("symbol", "exchange", name="uq_instruments_symbol_exchange"),
    )


class Bar(Base):
    """One time-bucketed OHLCV record."""

    __tablename__ = "bars"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.id"), nullable=False
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(_QTY, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("instrument_id", "interval", "timestamp", name="uq_bars_inst_int_ts"),
        Index("ix_bars_inst_int_ts", "instrument_id", "interval", "timestamp"),
    )


# ----------------------------------------------------------------------------
# Account, positions, orders, trades
# ----------------------------------------------------------------------------


class Account(Base):
    """A logical container for cash and positions.

    For `account_type='sim'`, the optional `saxo_*` columns link this row to
    the broker-side identity captured during OAuth and (for placement) the
    specific Saxo sub-account orders are routed into.

    - `saxo_client_key`: opaque token for the Saxo Client (organization or
      individual). Captured from `/port/v1/users/me` on first auth; used in
      audit + debugging.
    - `saxo_account_key`: opaque token for the specific sub-account within
      the Client. T-001-B will need this to place orders. Backfillable from
      `/port/v1/accounts/me` after auth.
    - `saxo_account_id`: human-readable account number from Saxo's UI (e.g.
      "22264911"). Not used in API calls; useful for display + linking to
      what the user sees in the Saxo portal.

    All three are nullable: a SIM account can exist before its keys are
    known, and `paper` accounts never have them set.
    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    account_type: Mapped[str] = mapped_column(String(8), nullable=False)  # paper|sim|live
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash: Mapped[Decimal] = mapped_column(_PRICE, nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    saxo_client_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    saxo_account_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    saxo_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Position(Base):
    """Current holding of an instrument in an account."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.id"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(_QTY, nullable=False, default=Decimal("0"))
    avg_cost: Mapped[Decimal] = mapped_column(_PRICE, nullable=False, default=Decimal("0"))
    tag: Mapped[str] = mapped_column(
        String(16), nullable=False, default="managed"
    )  # managed|view_only|untouchable
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "instrument_id", name="uq_positions_account_inst"),
    )


class Order(Base):
    """A request to a broker to execute a trade."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.id"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # buy|sell
    quantity: Mapped[Decimal] = mapped_column(_QTY, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # strategy / agent id
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    trades: Mapped[list[Trade]] = relationship("Trade", back_populates="order")


class Trade(Base):
    """A completed (fully or partially) execution of an order."""

    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    fill_quantity: Mapped[Decimal] = mapped_column(_QTY, nullable=False)
    fees: Mapped[Decimal] = mapped_column(_PRICE, nullable=False, default=Decimal("0"))
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped[Order] = relationship("Order", back_populates="trades")


# ----------------------------------------------------------------------------
# Agents and recommendations (introduced in PR 5)
# ----------------------------------------------------------------------------


class Agent(Base):
    """LLM-powered analyst agent configuration."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    personality: Mapped[str] = mapped_column(String(64), nullable=False)
    interests: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    environment: Mapped[str] = mapped_column(String(8), nullable=False, default="paper")
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Recommendation(Base):
    """A pending proposal from an agent awaiting user approval."""

    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False)
    signals: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending|approved|modified|rejected|expired|executed
    modifications: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ----------------------------------------------------------------------------
# OAuth (T-001-A — Saxo SIM)
# ----------------------------------------------------------------------------


class OAuthState(Base):
    """Short-lived PKCE state for an in-flight OAuth handshake.

    Created when the engine returns an authorize URL; consumed when the
    browser callback hits. The `state` parameter doubles as CSRF token and
    account demux key. Rows older than 10 minutes are stale.
    """

    __tablename__ = "oauth_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=False, index=True
    )
    broker: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthToken(Base):
    """Persisted, encrypted OAuth tokens per (account, broker).

    Tokens are Fernet-encrypted in code before insertion — see
    `snapd_invest.broker.saxo_oauth`. The DB never sees plaintext.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    broker: Mapped[str] = mapped_column(String(16), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "broker", name="uq_oauth_tokens_account_broker"),
    )
