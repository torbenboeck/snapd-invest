"""End-to-end Saxo SIM test. Skipped unless SAXO_RUN_LIVE_TESTS=1.

This is the only test in the suite that hits a real external service. It
verifies the full T-001-A loop end-to-end:

1. Read SNAPDINVEST_* settings from `engine/.env`.
2. Find the SIM account in the local SQLite DB.
3. Load the previously stored encrypted tokens (refresh proactively if needed).
4. Call Saxo SIM `/port/v1/users/me` and assert the response is parseable.

Pre-conditions (one-time, manual):
- `make init-keys` has run.
- A SIM `Account` row exists.
- `snapdinvest auth saxo --account <id>` has been run successfully.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from snapd_invest.broker import Filled, OrderRequest, Rejected
from snapd_invest.broker.saxo import SaxoBroker
from snapd_invest.broker.saxo_oauth import load_tokens
from snapd_invest.config import Settings
from snapd_invest.crypto import FernetCipher
from snapd_invest.data import ensure_saxo_instrument
from snapd_invest.models import Account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [
    pytest.mark.saxo_live,
    pytest.mark.skipif(
        os.environ.get("SAXO_RUN_LIVE_TESTS") != "1",
        reason="SAXO_RUN_LIVE_TESTS=1 not set",
    ),
]


class _RealClock:
    """Wall-clock for the live test; FakeClock would break refresh timing."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@pytest.fixture
async def live_session() -> AsyncIterator[AsyncSession]:
    """Open a session against the real on-disk SQLite DB (not the test in-memory one)."""
    settings = Settings()
    engine = create_async_engine(settings.db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


class TestSaxoLiveGetAccount:
    async def test_get_account_returns_real_user(self, live_session: AsyncSession) -> None:
        settings = Settings()
        assert settings.saxo_env == "sim", "Set SNAPDINVEST_SAXO_ENV=sim in engine/.env"
        assert settings.saxo_client_id is not None, "Set SNAPDINVEST_SAXO_CLIENT_ID in engine/.env"
        assert settings.encryption_key is not None, (
            "Run `make init-keys` to set SNAPDINVEST_ENCRYPTION_KEY"
        )

        cipher = FernetCipher(settings.encryption_key.encode("ascii"))

        account = (
            await live_session.execute(select(Account).where(Account.account_type == "sim"))
        ).scalar_one_or_none()
        assert account is not None, "No SIM account in the DB. Create one before running this test."

        stored = await load_tokens(live_session, cipher, account_id=account.id, broker="saxo")
        assert stored is not None, (
            "No tokens stored. Run `snapdinvest auth saxo --account <id>` first."
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            broker = SaxoBroker(
                client=client,
                clock=_RealClock(),
                cipher=cipher,
                client_id=settings.saxo_client_id,
                account_id=account.id,
            )
            info = await broker.get_account(live_session)

        assert info.client_key, "Saxo response missing ClientKey"
        assert info.user_key, "Saxo response missing UserKey"


class TestSaxoLivePlaceAndCancel:
    """Round-trip: resolve EURDKK → place a far-from-market limit order →
    list /orders/me → cancel → list /orders/me again.

    The limit is intentionally well below market (1.0 against typical ~7.47)
    so the order sits open long enough to read+cancel before any fill.
    """

    async def test_place_and_cancel_round_trip(self, live_session: AsyncSession) -> None:
        settings = Settings()
        assert settings.saxo_env == "sim"
        assert settings.saxo_client_id is not None
        assert settings.encryption_key is not None
        cipher = FernetCipher(settings.encryption_key.encode("ascii"))

        account = (
            await live_session.execute(select(Account).where(Account.account_type == "sim"))
        ).scalar_one_or_none()
        assert account is not None, "No SIM account; create one first."
        assert account.saxo_account_key is not None, (
            "Account missing saxo_account_key. Re-run `snapdinvest auth saxo --account <id>` "
            "so identity backfill populates it."
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            broker = SaxoBroker(
                client=client,
                clock=_RealClock(),
                cipher=cipher,
                client_id=settings.saxo_client_id,
                account_id=account.id,
            )

            instrument = await ensure_saxo_instrument(
                live_session, broker, symbol="EURDKK", exchange="FX"
            )
            await live_session.commit()
            assert instrument.saxo_uic is not None

            idempotency_key = f"sim-live-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            result = await broker.place_order(
                live_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="buy",
                    quantity=Decimal("1000"),
                    limit_price=Decimal("1.0"),
                    source="manual-cli",
                    idempotency_key=idempotency_key,
                ),
            )
            await live_session.commit()
            assert isinstance(result, Filled | Rejected), (
                f"Unexpected placement outcome: {type(result).__name__}"
            )
            if isinstance(result, Rejected):
                pytest.skip(f"Saxo rejected the test order (likely market closed): {result.reason}")

            open_orders = await broker.get_open_orders(live_session)
            placed = next((o for o in open_orders if o.external_reference == idempotency_key), None)
            assert placed is not None, (
                "Order placed but does not appear in /orders/me — check Saxo portal"
            )

            await broker.cancel_order(live_session, order_id=placed.order_id)

            still_open = await broker.get_open_orders(live_session)
            assert not any(o.order_id == placed.order_id for o in still_open), (
                "Order remained in /orders/me after cancel"
            )
