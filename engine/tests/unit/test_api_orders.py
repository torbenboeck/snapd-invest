"""Tests for POST /v1/orders (manual placement)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.api import (
    broker_factory_dep,
    clock_dep,
    create_app,
    promotion_gate_dep,
    risk_dep,
    saxo_http_client_dep,
    session_factory_dep,
    settings_dep,
)
from snapd_invest.broker import PaperBroker, SaxoBroker
from snapd_invest.broker.saxo import SAXO_SIM_API_BASE
from snapd_invest.broker.saxo_oauth import TokenSet, store_tokens
from snapd_invest.config import Settings
from snapd_invest.crypto import FernetCipher
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.portfolio import create_account
from snapd_invest.promotion import DeniedFor, trivial_promotion_gate
from snapd_invest.risk import RiskConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import IBroker
    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account


@pytest.fixture
def sim_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        saxo_env="sim",
        saxo_client_id="client-123",
        saxo_redirect_uri="http://localhost:8000/v1/oauth/saxo/callback",
        encryption_key=Fernet.generate_key().decode("ascii"),
    )


def _build_test_app(
    *,
    db_engine: object,
    fake_clock: FakeClock,
    settings: Settings,
    saxo_http_client: httpx.AsyncClient | None = None,
    promotion_gate=trivial_promotion_gate,
):
    app = create_app()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    paper_broker = PaperBroker(fake_clock)
    http_client = saxo_http_client or httpx.AsyncClient()

    cipher = (
        FernetCipher(settings.encryption_key.encode("ascii"))
        if settings.encryption_key is not None
        else None
    )

    def _factory(account: Account) -> IBroker:
        if account.account_type == "paper":
            return paper_broker
        if account.account_type == "sim":
            assert cipher is not None and settings.saxo_client_id is not None
            return SaxoBroker(
                client=http_client,
                clock=fake_clock,
                cipher=cipher,
                client_id=settings.saxo_client_id,
                account_id=account.id,
            )
        raise ValueError(f"unsupported account_type: {account.account_type}")

    risk_config = RiskConfig()

    app.dependency_overrides[settings_dep] = lambda: settings
    app.dependency_overrides[clock_dep] = lambda: fake_clock
    app.dependency_overrides[session_factory_dep] = lambda: factory
    app.dependency_overrides[saxo_http_client_dep] = lambda: http_client
    app.dependency_overrides[broker_factory_dep] = lambda: _factory
    app.dependency_overrides[promotion_gate_dep] = lambda: promotion_gate
    app.dependency_overrides[risk_dep] = lambda: risk_config
    return app


class TestPlaceOrderPaperHappyPath:
    async def test_paper_market_order_returns_filled(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(
            db_session,
            fake_clock,
            name="paper-main",
            account_type="paper",
            initial_cash=Decimal("100000"),
        )
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=[
                BarData(
                    instrument_symbol="AAPL",
                    interval="1d",
                    timestamp=datetime(2026, 5, 18, tzinfo=UTC),
                    open=Decimal("100"),
                    high=Decimal("100"),
                    low=Decimal("100"),
                    close=Decimal("100"),
                    volume=Decimal("1000"),
                )
            ],
            source="test",
        )
        await db_session.commit()

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/orders",
                json={
                    "account_id": account.id,
                    "instrument_symbol": "AAPL",
                    "instrument_exchange": "NASDAQ",
                    "side": "buy",
                    "quantity": "10",
                    "source": "manual-cli",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "filled"
        assert body["order_id"] is not None
        assert body["saxo_error_code"] is None

    async def test_unknown_account_returns_404(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/orders",
                json={
                    "account_id": "does-not-exist",
                    "instrument_symbol": "AAPL",
                    "instrument_exchange": "NASDAQ",
                    "side": "buy",
                    "quantity": "1",
                },
            )
        assert resp.status_code == 404


class TestPlaceOrderPromotionDenied:
    async def test_promotion_denied_returns_kind_denied(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(
            db_session,
            fake_clock,
            name="paper-main",
            account_type="paper",
            initial_cash=Decimal("100000"),
        )
        await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        await db_session.commit()

        def denying_gate(_account, _broker):
            return DeniedFor(reason="gate test denied")

        app = _build_test_app(
            db_engine=db_engine,
            fake_clock=fake_clock,
            settings=sim_settings,
            promotion_gate=denying_gate,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/orders",
                json={
                    "account_id": account.id,
                    "instrument_symbol": "AAPL",
                    "instrument_exchange": "NASDAQ",
                    "side": "buy",
                    "quantity": "1",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "promotion_denied"
        assert "gate test denied" in (body["reason"] or "")


class TestPlaceOrderSimRoutesThroughSaxoBroker:
    @respx.mock
    async def test_sim_order_calls_saxo_with_resolved_instrument(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        account = await create_account(
            db_session,
            fake_clock,
            name="sim",
            account_type="sim",
            initial_cash=Decimal("1000000"),
        )
        account.saxo_account_key = "acc-key-1"
        account.saxo_client_key = "client-key-abc"
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="good-token",
                refresh_token="r1",
                access_expires_at=fake_clock.now() + timedelta(seconds=600),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )
        await db_session.commit()

        # ensure_saxo_instrument is called for SIM accounts: it hits
        # /ref/v1/instruments to resolve the UIC.
        respx.get(f"{SAXO_SIM_API_BASE}/ref/v1/instruments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Identifier": 16,
                            "Symbol": "EURDKK",
                            "AssetType": "FxSpot",
                            "Description": "Euro/Danish Krone",
                            "CurrencyCode": "DKK",
                        },
                    ],
                },
            )
        )
        # Then place_order POSTs to /trade/v2/orders.
        place_route = respx.post(f"{SAXO_SIM_API_BASE}/trade/v2/orders").mock(
            return_value=httpx.Response(200, json={"OrderId": "5038292933"}),
        )

        # Use a limit order so the risk gate uses limit_price as its
        # reference; avoids needing to also mock /trade/v1/infoprices/list.
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/orders",
                json={
                    "account_id": account.id,
                    "instrument_symbol": "EURDKK",
                    "instrument_exchange": "FX",
                    "side": "buy",
                    # 1000 EUR @ 7.0 = 7000 DKK — under 20% of 1M DKK equity
                    "quantity": "1000",
                    "limit_price": "7.0",
                    "source": "manual-cli",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "filled"
        assert place_route.called

    async def test_sim_order_without_account_key_returns_401(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        # No saxo_account_key set; tokens not stored.
        _ = cipher
        await db_session.commit()

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/orders",
                json={
                    "account_id": account.id,
                    "instrument_symbol": "EURDKK",
                    "instrument_exchange": "FX",
                    "side": "buy",
                    "quantity": "100000",
                },
            )
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"]["code"] == "saxo_reauth_required"
