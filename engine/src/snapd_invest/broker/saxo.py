"""SaxoBroker — IBroker implementation against Saxo SIM.

T-001-A scope: `get_account()` only. Other methods (place_order, etc.)
arrive in T-001-B.

The broker accepts an `httpx.AsyncClient` (shared at the engine level so
connection pooling works), a `Clock`, a `Cipher`, the OAuth `client_id`,
and the `account_id` it represents. Tokens are loaded from `oauth_tokens`
on every call; this keeps the broker stateless and concurrency-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

from snapd_invest.broker import (
    BrokerAuthError,
    BrokerHttpError,
    BrokerTimeoutError,
    OrderRequest,
    OrderResult,
)
from snapd_invest.broker.saxo_oauth import (
    get_active_access_token,
    load_tokens,
    refresh_tokens,
    store_tokens,
)
from snapd_invest.models import Account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock
    from snapd_invest.crypto import Cipher
    from snapd_invest.models import Instrument

SAXO_SIM_API_BASE = "https://gateway.saxobank.com/sim/openapi"

HTTP_UNAUTHORIZED = 401
HTTP_BAD_REQUEST = 400


@dataclass(slots=True, frozen=True)
class SaxoAccountInfo:
    """Minimal Saxo `/port/v1/users/me` response.

    Other fields (Culture, ClientId, etc.) are ignored at MVP.
    """

    client_key: str
    user_key: str
    name: str


@dataclass(slots=True, frozen=True)
class SaxoInstrumentHit:
    """One result row from `/ref/v1/instruments`."""

    uic: int
    symbol: str
    asset_type: str
    description: str


@dataclass(slots=True, frozen=True)
class SaxoOpenOrder:
    """One open order from `/port/v1/orders/me`."""

    order_id: str
    uic: int
    symbol: str
    asset_type: str
    buy_sell: str
    amount: Decimal
    order_type: str
    duration_type: str
    external_reference: str | None


class SaxoBroker:
    """SaxoBroker — IBroker against Saxo SIM. T-001-A: get_account only."""

    venue_name = "saxo-sim"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        clock: Clock,
        cipher: Cipher,
        client_id: str,
        account_id: str,
    ) -> None:
        self._client = client
        self._clock = clock
        self._cipher = cipher
        self._client_id = client_id
        self._account_id = account_id

    async def get_account(self, session: AsyncSession) -> SaxoAccountInfo:
        """Fetch `/port/v1/users/me` for the SaxoBroker's account.

        Performs a reactive refresh once if the call comes back 401.
        """
        payload = await self._authed_get(session, "/port/v1/users/me")
        return SaxoAccountInfo(
            client_key=payload["ClientKey"],
            user_key=payload["UserKey"],
            name=payload.get("Name", ""),
        )

    async def search_instruments(
        self, session: AsyncSession, keywords: str, *, asset_type: str
    ) -> list[SaxoInstrumentHit]:
        """Search `/ref/v1/instruments` by keyword and asset type."""
        payload = await self._authed_get(
            session,
            f"/ref/v1/instruments?KeyWords={keywords}&AssetTypes={asset_type}",
        )
        return [
            SaxoInstrumentHit(
                uic=int(row["Identifier"]),
                symbol=row["Symbol"],
                asset_type=row["AssetType"],
                description=row.get("Description", ""),
            )
            for row in payload.get("Data", [])
        ]

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> OrderResult:
        """T-001-B will implement order placement against Saxo SIM."""
        raise NotImplementedError("SaxoBroker.place_order arrives in T-001-B (paper-only at MVP)")

    async def get_open_orders(self, session: AsyncSession) -> list[SaxoOpenOrder]:
        """List the currently open orders across all accounts under this user.

        Saxo's `/port/v1/orders/me` returns every working order for the
        authenticated user regardless of `AccountKey`. Filtering down to a
        specific account is a caller concern; the broker just maps the wire
        shape into typed rows.
        """
        payload = await self._authed_get(
            session,
            "/port/v1/orders/me?FieldGroups=DisplayAndFormat,ExchangeInfo",
        )
        return [
            SaxoOpenOrder(
                order_id=str(row["OrderId"]),
                uic=int(row["Uic"]),
                symbol=row.get("DisplayAndFormat", {}).get("Symbol", ""),
                asset_type=row["AssetType"],
                buy_sell=row["BuySell"],
                amount=Decimal(str(row["Amount"])),
                order_type=row["OpenOrderType"],
                duration_type=row.get("Duration", {}).get("DurationType", ""),
                external_reference=row.get("ExternalReference"),
            )
            for row in payload.get("Data", [])
        ]

    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None:
        """Fetch the mid of bid/ask for `instrument` via `/trade/v1/infoprices/list`.

        Returns None if Saxo returns no quote (e.g. closed market with no
        snapshot). Raises ValueError if the instrument hasn't been resolved
        against Saxo yet (call `ensure_saxo_instrument` first) and
        BrokerAuthError if the account is missing its `saxo_account_key`.
        """
        if instrument.saxo_uic is None:
            raise ValueError(
                f"instrument {instrument.symbol}@{instrument.exchange} has no saxo_uic; "
                "call ensure_saxo_instrument first"
            )
        account_key = await self._account_key(session)
        payload = await self._authed_get(
            session,
            f"/trade/v1/infoprices/list?AccountKey={account_key}"
            f"&Uics={instrument.saxo_uic}"
            f"&AssetType={instrument.saxo_asset_type}"
            f"&Amount=1"
            f"&FieldGroups=DisplayAndFormat,Quote",
        )
        data = payload.get("Data", [])
        if not data:
            return None
        quote = data[0].get("Quote", {})
        bid = quote.get("Bid")
        ask = quote.get("Ask")
        if bid is None or ask is None:
            return None
        return (Decimal(str(bid)) + Decimal(str(ask))) / Decimal(2)

    async def _account_key(self, session: AsyncSession) -> str:
        """Load this broker's account's `saxo_account_key` from the DB.

        Trading-side calls (place_order, get_last_price, cancel_order)
        require the AccountKey opaque token captured during OAuth identity
        backfill. If it's missing the user has to re-authenticate.
        """
        account = await session.get(Account, self._account_id)
        if account is None or account.saxo_account_key is None:
            raise BrokerAuthError(
                f"account {self._account_id} has no saxo_account_key; "
                "re-authenticate via 'snapdinvest auth saxo --account ...'"
            )
        return account.saxo_account_key

    async def _authed_request(
        self,
        session: AsyncSession,
        method: str,
        path: str,
        *,
        json: Any = None,
    ) -> dict[str, Any]:
        """Authenticated Saxo request with reactive-refresh-on-401.

        Generic over verb so GET / POST / DELETE share the same retry
        logic. Returns the parsed JSON body, or `{}` for empty responses
        (204 No Content / empty body on 200).
        """
        token = await get_active_access_token(
            session,
            self._clock,
            self._client,
            self._cipher,
            client_id=self._client_id,
            account_id=self._account_id,
            broker="saxo",
        )
        try:
            response = await self._client.request(
                method,
                f"{SAXO_SIM_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise BrokerTimeoutError(str(exc)) from exc

        if response.status_code == HTTP_UNAUTHORIZED:
            stored = await load_tokens(
                session, self._cipher, account_id=self._account_id, broker="saxo"
            )
            if stored is None:
                raise BrokerAuthError("401 from Saxo and no stored tokens to refresh")
            fresh = await refresh_tokens(
                self._client,
                self._clock,
                client_id=self._client_id,
                refresh_token=stored.refresh_token,
            )
            await store_tokens(
                session,
                self._clock,
                self._cipher,
                account_id=self._account_id,
                broker="saxo",
                tokens=fresh,
            )
            response = await self._client.request(
                method,
                f"{SAXO_SIM_API_BASE}{path}",
                headers={"Authorization": f"Bearer {fresh.access_token}"},
                json=json,
            )
            if response.status_code == HTTP_UNAUTHORIZED:
                raise BrokerAuthError("401 persisted after refresh")

        if response.status_code >= HTTP_BAD_REQUEST:
            raise BrokerHttpError(status_code=response.status_code, body=response.text)

        if not response.content:
            return {}
        result: dict[str, Any] = response.json()
        return result

    async def _authed_get(self, session: AsyncSession, path: str) -> dict[str, Any]:
        return await self._authed_request(session, "GET", path)

    async def _authed_post(self, session: AsyncSession, path: str, *, json: Any) -> dict[str, Any]:
        return await self._authed_request(session, "POST", path, json=json)

    async def _authed_delete(self, session: AsyncSession, path: str) -> dict[str, Any]:
        return await self._authed_request(session, "DELETE", path)
