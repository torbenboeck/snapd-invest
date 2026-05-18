"""Tests for `snapd_invest.broker.saxo.SaxoBroker`."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from snapd_invest.broker import BrokerAuthError, BrokerHttpError
from snapd_invest.broker.saxo import SAXO_SIM_API_BASE, SaxoBroker, SaxoInstrumentHit
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    TokenSet,
    load_tokens,
    store_tokens,
)
from snapd_invest.crypto import FernetCipher
from snapd_invest.portfolio import create_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


ACCOUNTS_ME_URL = f"{SAXO_SIM_API_BASE}/port/v1/users/me"


async def _seed_tokens(
    db_session: AsyncSession, fake_clock: FakeClock, cipher: FernetCipher
) -> str:
    account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
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
