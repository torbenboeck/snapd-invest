"""Tests for `snapd_invest.broker.saxo.SaxoBroker`."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from snapd_invest.broker import BrokerAuthError, BrokerHttpError
from snapd_invest.broker.saxo import (
    SAXO_SIM_API_BASE,
    SaxoBroker,
    SaxoInstrumentHit,
    SaxoOpenOrder,
)
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    TokenSet,
    load_tokens,
    store_tokens,
)
from snapd_invest.crypto import FernetCipher
from snapd_invest.models import Account, Instrument
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
