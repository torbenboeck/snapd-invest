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

if TYPE_CHECKING:
    from decimal import Decimal

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

    async def place_order(self, session: AsyncSession, request: OrderRequest) -> OrderResult:
        """T-001-B will implement order placement against Saxo SIM."""
        raise NotImplementedError("SaxoBroker.place_order arrives in T-001-B (paper-only at MVP)")

    async def get_last_price(
        self, session: AsyncSession, *, instrument: Instrument
    ) -> Decimal | None:
        """T-001-B will implement price fetching against Saxo SIM."""
        raise NotImplementedError(
            "SaxoBroker.get_last_price arrives in T-001-B (paper-only at MVP)"
        )

    async def _authed_get(self, session: AsyncSession, path: str) -> dict[str, Any]:
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
            response = await self._client.get(
                f"{SAXO_SIM_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
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
            response = await self._client.get(
                f"{SAXO_SIM_API_BASE}{path}",
                headers={"Authorization": f"Bearer {fresh.access_token}"},
            )
            if response.status_code == HTTP_UNAUTHORIZED:
                raise BrokerAuthError("401 persisted after refresh")

        if response.status_code >= HTTP_BAD_REQUEST:
            raise BrokerHttpError(status_code=response.status_code, body=response.text)

        result: dict[str, Any] = response.json()
        return result
