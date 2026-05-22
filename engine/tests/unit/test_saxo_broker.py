"""Tests for `snapd_invest.broker.saxo.SaxoBroker`."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import select

from snapd_invest.broker import (
    BrokerAuthError,
    BrokerHttpError,
    Filled,
    IdempotentReplay,
    OrderRequest,
    Rejected,
)
from snapd_invest.broker.saxo import (
    SAXO_SIM_API_BASE,
    SaxoBroker,
    SaxoInstrumentHit,
    SaxoOpenOrder,
    SaxoPosition,
)
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    TokenSet,
    load_tokens,
    store_tokens,
)
from snapd_invest.crypto import FernetCipher
from snapd_invest.models import Account, Instrument, Order, new_id
from snapd_invest.portfolio import create_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


ACCOUNTS_ME_URL = f"{SAXO_SIM_API_BASE}/port/v1/users/me"


async def _seed_tokens(
    db_session: AsyncSession,
    fake_clock: FakeClock,
    cipher: FernetCipher,
    *,
    saxo_account_key: str | None = None,
) -> str:
    account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
    if saxo_account_key is not None:
        account.saxo_account_key = saxo_account_key
        await db_session.flush()
    await store_tokens(
        db_session,
        fake_clock,
        cipher,
        account_id=account.id,
        broker="saxo",
        tokens=TokenSet(
            access_token="good-token",
            refresh_token="refresh-1",
            access_expires_at=fake_clock.now() + timedelta(seconds=600),
            refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
        ),
    )
    return account.id


class TestSaxoBrokerGetAccount:
    @respx.mock
    async def test_happy_path(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(ACCOUNTS_ME_URL).mock(
            return_value=httpx.Response(
                200,
                json={"ClientKey": "client-abc", "UserKey": "user-xyz", "Name": "Torben"},
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="client-123",
                account_id=account_id,
            )
            info = await broker.get_account(db_session)

        assert info.client_key == "client-abc"
        assert info.user_key == "user-xyz"
        assert info.name == "Torben"

    @respx.mock
    async def test_reactive_refresh_on_401_then_succeeds(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)

        respx.get(ACCOUNTS_ME_URL).mock(
            side_effect=[
                httpx.Response(401, json={"error": "expired"}),
                httpx.Response(200, json={"ClientKey": "ck", "UserKey": "uk", "Name": "x"}),
            ]
        )
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "refreshed",
                    "refresh_token": "r2",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="client-123",
                account_id=account_id,
            )
            info = await broker.get_account(db_session)

        assert info.client_key == "ck"
        stored = await load_tokens(db_session, cipher, account_id=account_id, broker="saxo")
        assert stored is not None
        assert stored.access_token == "refreshed"

    @respx.mock
    async def test_401_then_refresh_fails_raises_auth_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(ACCOUNTS_ME_URL).mock(return_value=httpx.Response(401))
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="client-123",
                account_id=account_id,
            )
            with pytest.raises(BrokerAuthError):
                await broker.get_account(db_session)

    @respx.mock
    async def test_500_raises_http_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(ACCOUNTS_ME_URL).mock(return_value=httpx.Response(503, text="upstream"))

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="client-123",
                account_id=account_id,
            )
            with pytest.raises(BrokerHttpError) as exc_info:
                await broker.get_account(db_session)
        assert exc_info.value.status_code == 503


INSTRUMENTS_URL = f"{SAXO_SIM_API_BASE}/ref/v1/instruments"


class TestSaxoBrokerSearchInstruments:
    @respx.mock
    async def test_search_instruments_happy_path(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(INSTRUMENTS_URL).mock(
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
        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            results = await broker.search_instruments(db_session, "EURDKK", asset_type="FxSpot")

        assert len(results) == 1
        assert results[0].uic == 16
        assert results[0].symbol == "EURDKK"
        assert results[0].asset_type == "FxSpot"
        assert results[0].description == "Euro/Danish Krone"
        assert isinstance(results[0], SaxoInstrumentHit)

    @respx.mock
    async def test_search_instruments_empty_data_returns_empty_list(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(INSTRUMENTS_URL).mock(return_value=httpx.Response(200, json={"Data": []}))
        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            results = await broker.search_instruments(db_session, "UNKNOWN", asset_type="Stock")

        assert results == []


INFOPRICES_URL = f"{SAXO_SIM_API_BASE}/trade/v1/infoprices/list"


def _eurdkk_instrument(*, saxo_uic: int | None = 16) -> Instrument:
    return Instrument(
        symbol="EURDKK",
        exchange="FX",
        instrument_type="fx",
        currency="DKK",
        tick_size=Decimal("0.00001"),
        saxo_uic=saxo_uic,
        saxo_asset_type="FxSpot" if saxo_uic is not None else None,
        saxo_currency_decimals=4 if saxo_uic is not None else None,
    )


class TestSaxoBrokerGetLastPrice:
    @respx.mock
    async def test_get_last_price_returns_mid_of_bid_ask(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.get(INFOPRICES_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Uic": 16,
                            "AssetType": "FxSpot",
                            "Quote": {"Bid": 7.47335, "Ask": 7.47385, "Mid": 7.47360},
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            price = await broker.get_last_price(db_session, instrument=_eurdkk_instrument())

        assert price == Decimal("7.47360")

    @respx.mock
    async def test_get_last_price_empty_data_returns_none(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.get(INFOPRICES_URL).mock(return_value=httpx.Response(200, json={"Data": []}))

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            price = await broker.get_last_price(db_session, instrument=_eurdkk_instrument())

        assert price is None

    async def test_get_last_price_missing_saxo_uic_raises(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(ValueError, match="saxo_uic"):
                await broker.get_last_price(
                    db_session, instrument=_eurdkk_instrument(saxo_uic=None)
                )

    async def test_get_last_price_missing_saxo_account_key_raises_auth_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(BrokerAuthError, match="saxo_account_key"):
                await broker.get_last_price(db_session, instrument=_eurdkk_instrument())

    async def test_account_loaded_for_each_call(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """Sanity: Account row carries the saxo_account_key the broker reads."""
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        account = await db_session.get(Account, account_id)
        assert account is not None
        assert account.saxo_account_key == "acc-key-1"


OPEN_ORDERS_URL = f"{SAXO_SIM_API_BASE}/port/v1/orders/me"


class TestSaxoBrokerGetOpenOrders:
    @respx.mock
    async def test_parses_two_open_orders(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(OPEN_ORDERS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "OrderId": "5038292933",
                            "Uic": 16,
                            "AssetType": "FxSpot",
                            "BuySell": "Buy",
                            "Amount": 100000,
                            "OpenOrderType": "Limit",
                            "Duration": {"DurationType": "GoodTillCancel"},
                            "ExternalReference": "idemp-1",
                            "DisplayAndFormat": {"Symbol": "EURDKK", "Currency": "DKK"},
                        },
                        {
                            "OrderId": "5038292934",
                            "Uic": 21,
                            "AssetType": "FxSpot",
                            "BuySell": "Sell",
                            "Amount": 25000,
                            "OpenOrderType": "Market",
                            "Duration": {"DurationType": "DayOrder"},
                            "ExternalReference": None,
                            "DisplayAndFormat": {"Symbol": "EURUSD", "Currency": "USD"},
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            orders = await broker.get_open_orders(db_session)

        assert len(orders) == 2
        assert isinstance(orders[0], SaxoOpenOrder)
        assert orders[0].order_id == "5038292933"
        assert orders[0].uic == 16
        assert orders[0].symbol == "EURDKK"
        assert orders[0].asset_type == "FxSpot"
        assert orders[0].buy_sell == "Buy"
        assert orders[0].amount == Decimal("100000")
        assert orders[0].order_type == "Limit"
        assert orders[0].duration_type == "GoodTillCancel"
        assert orders[0].external_reference == "idemp-1"
        assert orders[1].duration_type == "DayOrder"
        assert orders[1].external_reference is None

    @respx.mock
    async def test_returns_empty_list_when_no_orders(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)
        respx.get(OPEN_ORDERS_URL).mock(return_value=httpx.Response(200, json={"Data": []}))

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            orders = await broker.get_open_orders(db_session)

        assert orders == []


PROBE_PATH = "/trade/v2/orders"
PROBE_URL = f"{SAXO_SIM_API_BASE}{PROBE_PATH}"
ORDER_DELETE_URL_RE = rf"{SAXO_SIM_API_BASE}/trade/v2/orders/.+\?AccountKey=.+"


class TestAuthedRequestRefreshAcrossVerbs:
    """The 401-then-refresh path on POST + DELETE — the GET version is
    already covered by TestSaxoBrokerGetAccount.test_reactive_refresh_*.
    """

    @respx.mock
    async def test_post_401_triggers_refresh_then_succeeds(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)

        respx.post(PROBE_URL).mock(
            side_effect=[
                httpx.Response(401, json={"error": "expired"}),
                httpx.Response(200, json={"OrderId": "5038"}),
            ]
        )
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "refreshed",
                    "refresh_token": "r2",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            payload = await broker._authed_post(db_session, PROBE_PATH, json={"x": 1})

        assert payload == {"OrderId": "5038"}
        stored = await load_tokens(db_session, cipher, account_id=account_id, broker="saxo")
        assert stored is not None
        assert stored.access_token == "refreshed"

    @respx.mock
    async def test_delete_401_triggers_refresh_then_succeeds(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)

        respx.delete(PROBE_URL).mock(
            side_effect=[
                httpx.Response(401, json={"error": "expired"}),
                httpx.Response(204),
            ]
        )
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "refreshed",
                    "refresh_token": "r2",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            payload = await broker._authed_delete(db_session, PROBE_PATH)

        # 204 No Content → caller gets {}; refresh did happen.
        assert payload == {}
        stored = await load_tokens(db_session, cipher, account_id=account_id, broker="saxo")
        assert stored is not None
        assert stored.access_token == "refreshed"


class TestSaxoBrokerCancelOrder:
    @respx.mock
    async def test_cancel_order_happy_path_returns_none(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        route = respx.delete(url__regex=ORDER_DELETE_URL_RE).mock(
            return_value=httpx.Response(200, json={"OrderId": "5038292933"}),
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            result = await broker.cancel_order(db_session, order_id="5038292933")

        assert result is None
        assert route.called
        # AccountKey carried through the URL
        assert "AccountKey=acc-key-1" in str(route.calls.last.request.url)
        assert "/trade/v2/orders/5038292933" in str(route.calls.last.request.url)

    @respx.mock
    async def test_cancel_order_404_raises_http_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.delete(url__regex=ORDER_DELETE_URL_RE).mock(
            return_value=httpx.Response(404, text="not found"),
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(BrokerHttpError) as exc_info:
                await broker.cancel_order(db_session, order_id="missing")
        assert exc_info.value.status_code == 404


PLACE_ORDER_URL = f"{SAXO_SIM_API_BASE}/trade/v2/orders"


async def _seed_instrument(db_session: AsyncSession) -> Instrument:
    inst = Instrument(
        symbol="EURDKK",
        exchange="FX",
        instrument_type="fx",
        currency="DKK",
        tick_size=Decimal("0.00001"),
        saxo_uic=16,
        saxo_asset_type="FxSpot",
        saxo_currency_decimals=4,
    )
    db_session.add(inst)
    await db_session.flush()
    return inst


async def _seed_account_with_key(
    db_session: AsyncSession,
    fake_clock: FakeClock,
    cipher: FernetCipher,
    *,
    saxo_account_key: str = "acc-key-1",
) -> tuple[str, Account]:
    account_id = await _seed_tokens(
        db_session, fake_clock, cipher, saxo_account_key=saxo_account_key
    )
    account = await db_session.get(Account, account_id)
    assert account is not None
    return account_id, account


class TestSaxoBrokerPlaceOrder:
    @respx.mock
    async def test_market_order_happy_path(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        instrument = await _seed_instrument(db_session)
        await db_session.flush()

        route = respx.post(PLACE_ORDER_URL).mock(
            return_value=httpx.Response(200, json={"OrderId": "5038292933"}),
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            result = await broker.place_order(
                db_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="buy",
                    quantity=Decimal("100000"),
                    limit_price=None,
                    source="microtrader",
                    idempotency_key="idemp-1",
                ),
            )

        assert isinstance(result, Filled)
        assert result.order.idempotency_key == "idemp-1"
        assert result.order.side == "buy"
        assert result.order.quantity == Decimal("100000")
        assert result.order.limit_price is None
        assert result.order.status == "filled"
        # Body shape per docs/integrations/saxo-openapi-notes.md
        sent_body = route.calls.last.request.read()
        body = json.loads(sent_body)
        assert body["Uic"] == 16
        assert body["BuySell"] == "Buy"
        assert body["AssetType"] == "FxSpot"
        assert body["Amount"] == 100000
        assert body["OrderType"] == "Market"
        assert body["OrderRelation"] == "StandAlone"
        assert body["AccountKey"] == "acc-key-1"
        assert body["ExternalReference"] == "idemp-1"
        assert body["ManualOrder"] is False

    @respx.mock
    async def test_limit_order_sets_order_price_and_duration(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        instrument = await _seed_instrument(db_session)
        await db_session.flush()

        route = respx.post(PLACE_ORDER_URL).mock(
            return_value=httpx.Response(200, json={"OrderId": "5038292934"}),
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            result = await broker.place_order(
                db_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="sell",
                    quantity=Decimal("25000"),
                    limit_price=Decimal("7.5"),
                    source="manual-cli",
                    idempotency_key="idemp-2",
                ),
            )

        assert isinstance(result, Filled)
        body = json.loads(route.calls.last.request.read())
        assert body["OrderType"] == "Limit"
        assert Decimal(str(body["OrderPrice"])) == Decimal("7.5")
        assert body["OrderDuration"] == {"DurationType": "GoodTillCancel"}
        assert body["BuySell"] == "Sell"
        # manual source flips ManualOrder
        assert body["ManualOrder"] is True

    @respx.mock
    async def test_saxo_returns_market_closed_returns_rejected(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        instrument = await _seed_instrument(db_session)
        await db_session.flush()

        respx.post(PLACE_ORDER_URL).mock(
            return_value=httpx.Response(
                400,
                json={"ErrorCode": "MarketClosed", "Message": "Market is closed"},
            ),
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            result = await broker.place_order(
                db_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="buy",
                    quantity=Decimal("100000"),
                    limit_price=None,
                    source="microtrader",
                    idempotency_key="idemp-3",
                ),
            )

        assert isinstance(result, Rejected)
        assert result.saxo_error_code == "MarketClosed"
        assert "closed" in result.reason.lower()
        # Order row persisted with status=rejected so audit can reproduce.
        order = (
            await db_session.execute(select(Order).where(Order.idempotency_key == "idemp-3"))
        ).scalar_one()
        assert order.status == "rejected"

    async def test_place_order_missing_saxo_uic_raises(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        bare_instrument = Instrument(
            symbol="UNKNOWN",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
            tick_size=Decimal("0.01"),
        )
        db_session.add(bare_instrument)
        await db_session.flush()

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            with pytest.raises(ValueError, match="saxo_uic"):
                await broker.place_order(
                    db_session,
                    OrderRequest(
                        account=account,
                        instrument=bare_instrument,
                        side="buy",
                        quantity=Decimal("1"),
                        limit_price=None,
                        source="microtrader",
                        idempotency_key="idemp-no-uic",
                    ),
                )


class TestSaxoBrokerPlaceOrderIdempotentReplay:
    @respx.mock
    async def test_replay_with_terminal_filled_order_returns_replay_without_saxo_call(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        instrument = await _seed_instrument(db_session)

        # Seed a pre-existing terminal Order row with the same key.
        existing = Order(
            id=new_id(),
            account_id=account.id,
            instrument_id=instrument.id,
            side="buy",
            quantity=Decimal("100000"),
            limit_price=None,
            status="filled",
            idempotency_key="replay-key-1",
            source="microtrader",
            submitted_at=fake_clock.now(),
        )
        db_session.add(existing)
        await db_session.flush()

        # respx with no mocked POST = any POST raises. So if the broker
        # respects idempotency it won't call Saxo at all.
        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            result = await broker.place_order(
                db_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="buy",
                    quantity=Decimal("100000"),
                    limit_price=None,
                    source="microtrader",
                    idempotency_key="replay-key-1",
                ),
            )

        assert isinstance(result, IdempotentReplay)
        assert result.original_idempotency_key == "replay-key-1"
        assert result.order.id == existing.id
        # Ensure no second Order row was created.
        rows = (
            (await db_session.execute(select(Order).where(Order.idempotency_key == "replay-key-1")))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    @respx.mock
    async def test_replay_with_pending_order_reconciles_via_saxo(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """When our row is non-terminal, query Saxo's /orders/me to find the
        external order and flip our row to terminal. Returns IdempotentReplay
        either way — caller should not re-POST.
        """
        cipher = FernetCipher(Fernet.generate_key())
        _, account = await _seed_account_with_key(db_session, fake_clock, cipher)
        instrument = await _seed_instrument(db_session)

        existing = Order(
            id=new_id(),
            account_id=account.id,
            instrument_id=instrument.id,
            side="buy",
            quantity=Decimal("100000"),
            limit_price=None,
            status="pending",
            idempotency_key="replay-key-pending",
            source="microtrader",
            submitted_at=fake_clock.now(),
        )
        db_session.add(existing)
        await db_session.flush()

        respx.get(OPEN_ORDERS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "OrderId": "5038292999",
                            "Uic": 16,
                            "AssetType": "FxSpot",
                            "BuySell": "Buy",
                            "Amount": 100000,
                            "OpenOrderType": "Market",
                            "Duration": {"DurationType": "DayOrder"},
                            "ExternalReference": "replay-key-pending",
                            "DisplayAndFormat": {"Symbol": "EURDKK"},
                        }
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account.id,
            )
            result = await broker.place_order(
                db_session,
                OrderRequest(
                    account=account,
                    instrument=instrument,
                    side="buy",
                    quantity=Decimal("100000"),
                    limit_price=None,
                    source="microtrader",
                    idempotency_key="replay-key-pending",
                ),
            )

        assert isinstance(result, IdempotentReplay)
        assert result.original_idempotency_key == "replay-key-pending"
        # Pending row should be flipped to terminal after reconciliation.
        await db_session.refresh(existing)
        assert existing.status == "filled"


POSITIONS_URL = f"{SAXO_SIM_API_BASE}/port/v1/positions"


class TestSaxoBrokerGetPositions:
    @respx.mock
    async def test_parses_two_positions(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        account = await db_session.get(Account, account_id)
        assert account is not None
        account.saxo_client_key = "client-key-abc"
        await db_session.flush()

        respx.get(POSITIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "PositionId": "p-1",
                            "PositionBase": {
                                "Uic": 16,
                                "AssetType": "FxSpot",
                                "Amount": 100000,
                                "OpenPrice": 7.47385,
                            },
                            "DisplayAndFormat": {"Symbol": "EURDKK", "Currency": "DKK"},
                        },
                        {
                            "PositionId": "p-2",
                            "PositionBase": {
                                "Uic": 21,
                                "AssetType": "FxSpot",
                                "Amount": -25000,
                                "OpenPrice": 1.08,
                            },
                            "DisplayAndFormat": {"Symbol": "EURUSD", "Currency": "USD"},
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            positions = await broker.get_positions(db_session, account=account)

        assert len(positions) == 2
        assert isinstance(positions[0], SaxoPosition)
        assert positions[0].uic == 16
        assert positions[0].symbol == "EURDKK"
        assert positions[0].asset_type == "FxSpot"
        assert positions[0].amount == Decimal("100000")
        assert positions[0].open_price == Decimal("7.47385")
        assert positions[0].currency == "DKK"
        assert positions[1].amount == Decimal("-25000")

    @respx.mock
    async def test_returns_empty_when_no_positions(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        account = await db_session.get(Account, account_id)
        assert account is not None
        account.saxo_client_key = "client-key-abc"
        await db_session.flush()

        respx.get(POSITIONS_URL).mock(return_value=httpx.Response(200, json={"Data": []}))

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            positions = await broker.get_positions(db_session, account=account)

        assert positions == []

    async def test_missing_saxo_client_key_raises_auth_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        account = await db_session.get(Account, account_id)
        assert account is not None

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(BrokerAuthError, match="saxo_client_key"):
                await broker.get_positions(db_session, account=account)


CHARTS_URL = f"{SAXO_SIM_API_BASE}/chart/v3/charts"


class TestSaxoBrokerGetCharts:
    @respx.mock
    async def test_fx_returns_mid_of_bid_ask(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """FxSpot rows carry OpenBid/OpenAsk etc.; we average to mid."""
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.get(CHARTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Time": "2026-05-20T00:00:00.000000Z",
                            "OpenBid": 7.4560,
                            "OpenAsk": 7.4564,
                            "HighBid": 7.4571,
                            "HighAsk": 7.4575,
                            "LowBid": 7.4555,
                            "LowAsk": 7.4559,
                            "CloseBid": 7.4565,
                            "CloseAsk": 7.4569,
                        },
                        {
                            "Time": "2026-05-21T00:00:00.000000Z",
                            "OpenBid": 7.4566,
                            "OpenAsk": 7.4570,
                            "HighBid": 7.4580,
                            "HighAsk": 7.4584,
                            "LowBid": 7.4562,
                            "LowAsk": 7.4566,
                            "CloseBid": 7.4575,
                            "CloseAsk": 7.4579,
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            bars = await broker.get_charts(
                db_session,
                instrument=_eurdkk_instrument(),
                interval="1d",
                count=2,
            )

        assert len(bars) == 2
        # Mid of OpenBid + OpenAsk for the first row.
        assert bars[0].open == Decimal("7.4562")
        assert bars[0].close == Decimal("7.4567")
        assert bars[0].volume == Decimal("0")
        assert bars[0].interval == "1d"
        assert bars[0].instrument_symbol == "EURDKK"
        assert bars[1].close == Decimal("7.4577")

    @respx.mock
    async def test_stock_uses_single_ohlc(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """Stock chart rows carry plain Open/High/Low/Close with a Volume."""
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.get(CHARTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Time": "2026-05-21T13:30:00.000000Z",
                            "Open": 180.5,
                            "High": 181.2,
                            "Low": 179.8,
                            "Close": 180.9,
                            "Volume": 5_312_400,
                        },
                    ],
                },
            )
        )

        instrument = Instrument(
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
            tick_size=Decimal("0.01"),
            saxo_uic=211,
            saxo_asset_type="Stock",
        )
        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            bars = await broker.get_charts(db_session, instrument=instrument, interval="1h")

        assert len(bars) == 1
        assert bars[0].open == Decimal("180.5")
        assert bars[0].close == Decimal("180.9")
        assert bars[0].volume == Decimal("5312400")
        assert bars[0].interval == "1h"

    @respx.mock
    async def test_empty_data_returns_empty_list(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )
        respx.get(CHARTS_URL).mock(return_value=httpx.Response(200, json={"Data": []}))

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            bars = await broker.get_charts(db_session, instrument=_eurdkk_instrument())

        assert bars == []

    async def test_missing_saxo_uic_raises_value_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(db_session, fake_clock, cipher)

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(ValueError, match="saxo_uic"):
                await broker.get_charts(db_session, instrument=_eurdkk_instrument(saxo_uic=None))

    async def test_unsupported_interval_raises_value_error(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account_id = await _seed_tokens(
            db_session, fake_clock, cipher, saxo_account_key="acc-key-1"
        )

        async with httpx.AsyncClient() as client:
            broker = SaxoBroker(
                client=client,
                clock=fake_clock,
                cipher=cipher,
                client_id="x",
                account_id=account_id,
            )
            with pytest.raises(ValueError, match="unsupported interval"):
                await broker.get_charts(db_session, instrument=_eurdkk_instrument(), interval="7m")
