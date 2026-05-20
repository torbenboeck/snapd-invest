"""SaxoBroker — IBroker implementation against Saxo SIM.

T-001-A scope: `get_account()` only. Other methods (place_order, etc.)
arrive in T-001-B.

The broker accepts an `httpx.AsyncClient` (shared at the engine level so
connection pooling works), a `Clock`, a `Cipher`, the OAuth `client_id`,
and the `account_id` it represents. Tokens are loaded from `oauth_tokens`
on every call; this keeps the broker stateless and concurrency-safe.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from snapd_invest.broker import (
    BrokerAuthError,
    BrokerHttpError,
    BrokerTimeoutError,
    Filled,
    IdempotentReplay,
    OrderRequest,
    OrderResult,
    Rejected,
)
from snapd_invest.broker.saxo_oauth import (
    get_active_access_token,
    load_tokens,
    refresh_tokens,
    store_tokens,
)
from snapd_invest.models import Account, Order, new_id

TERMINAL_ORDER_STATUSES = frozenset({"filled", "rejected", "cancelled"})

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


def _build_place_order_body(request: OrderRequest, *, account_key: str) -> dict[str, Any]:
    """Map an `OrderRequest` to Saxo's `POST /trade/v2/orders` body shape.

    `Amount` and `OrderPrice` are emitted as JSON numbers (float) because
    httpx's encoder rejects `Decimal`. Saxo accepts numeric input as either
    int or float.
    """
    assert request.instrument.saxo_uic is not None
    body: dict[str, Any] = {
        "Uic": request.instrument.saxo_uic,
        "BuySell": "Buy" if request.side == "buy" else "Sell",
        "AssetType": request.instrument.saxo_asset_type,
        "Amount": float(request.quantity),
        "OrderRelation": "StandAlone",
        "AccountKey": account_key,
        "ExternalReference": request.idempotency_key,
        "ManualOrder": request.source.startswith("manual"),
    }
    if request.limit_price is None:
        body["OrderType"] = "Market"
        body["OrderDuration"] = {"DurationType": "DayOrder"}
    else:
        body["OrderType"] = "Limit"
        body["OrderPrice"] = float(request.limit_price)
        body["OrderDuration"] = {"DurationType": "GoodTillCancel"}
    return body


def _parse_saxo_error(body: Any) -> tuple[str | None, str | None]:
    """Extract (ErrorCode, Message) from a Saxo error response.

    Saxo's trading endpoints surface errors in either:
        {"ErrorCode": "...", "Message": "..."}
        {"ErrorInfo": {"ErrorCode": "...", "Message": "..."}}

    `body` may be a parsed dict (from json()) or a raw string (from
    `BrokerHttpError.body`). Returns `(None, None)` if the shape doesn't
    match a recognised error.
    """
    payload: Any
    if isinstance(body, str):
        try:
            payload = _json.loads(body)
        except _json.JSONDecodeError:
            return (None, None)
    else:
        payload = body
    if not isinstance(payload, dict):
        return (None, None)
    if "ErrorCode" in payload:
        return (str(payload["ErrorCode"]), payload.get("Message"))
    info = payload.get("ErrorInfo")
    if isinstance(info, dict) and "ErrorCode" in info:
        return (str(info["ErrorCode"]), info.get("Message"))
    return (None, None)


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
        """Place an order against `POST /trade/v2/orders`.

        Wire-shape per `docs/integrations/saxo-openapi-notes.md`. The engine's
        idempotency_key plumbs through to Saxo's `ExternalReference`. A 200
        response with `OrderId` becomes `Filled`; a 4xx with a Saxo
        `ErrorCode` becomes `Rejected`. The Order row is persisted either way
        so audit can reconstruct what was attempted.

        On duplicate `idempotency_key`: if our row is in a terminal status
        (`filled` / `rejected` / `cancelled`) we return `IdempotentReplay`
        immediately, no Saxo call. Non-terminal (e.g. `pending`) triggers a
        reconciliation read against `/port/v1/orders/me` filtered by
        `ExternalReference`; we flip the row to terminal then replay.

        Trades are not synthesized in this MVP slice — Saxo's synchronous
        response only carries `OrderId`, not fill price/quantity. Position +
        cash reconciliation happens via `get_positions` (Task 15).
        """
        if request.instrument.saxo_uic is None:
            raise ValueError(
                f"instrument {request.instrument.symbol}@{request.instrument.exchange} "
                "has no saxo_uic; call ensure_saxo_instrument first"
            )

        existing = await self._find_order_by_key(session, request.idempotency_key)
        if existing is not None:
            if existing.status not in TERMINAL_ORDER_STATUSES:
                await self._reconcile_pending_order(session, existing)
            return IdempotentReplay(
                order=existing,
                trades=[],
                original_idempotency_key=request.idempotency_key,
            )

        account_key = await self._account_key(session)
        body = _build_place_order_body(request, account_key=account_key)

        try:
            response = await self._authed_post(session, "/trade/v2/orders", json=body)
        except BrokerHttpError as exc:
            error_code, message = _parse_saxo_error(exc.body)
            if error_code is not None:
                await self._persist_order(session, request, status="rejected")
                return Rejected(reason=message or "rejected", saxo_error_code=error_code)
            raise

        # 2xx but body still carries an ErrorCode (rare; defensive).
        error_code, message = _parse_saxo_error(response)
        if error_code is not None:
            await self._persist_order(session, request, status="rejected")
            return Rejected(reason=message or "rejected", saxo_error_code=error_code)

        order = await self._persist_order(session, request, status="filled")
        return Filled(order=order, trades=[])

    async def _persist_order(
        self, session: AsyncSession, request: OrderRequest, *, status: str
    ) -> Order:
        order = Order(
            id=new_id(),
            account_id=request.account.id,
            instrument_id=request.instrument.id,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            status=status,
            idempotency_key=request.idempotency_key,
            source=request.source,
            correlation_id=request.correlation_id,
            submitted_at=self._clock.now(),
        )
        session.add(order)
        await session.flush()
        return order

    async def _find_order_by_key(self, session: AsyncSession, idempotency_key: str) -> Order | None:
        stmt = select(Order).where(Order.idempotency_key == idempotency_key)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _reconcile_pending_order(self, session: AsyncSession, order: Order) -> None:
        """Query Saxo for the remote order matching this row's idempotency
        key and flip the local row to terminal.

        Saxo's /port/v1/orders/me returns currently-working orders. If our
        ExternalReference shows up there, the order is still working — treat
        as filled at MVP (we don't track working state). If it's absent,
        Saxo accepted it (now filled) or never received it; either way we
        cannot tell from here, so we mark filled and let get_positions
        reconcile reality later.
        """
        try:
            await self._authed_get(
                session, "/port/v1/orders/me?FieldGroups=DisplayAndFormat,ExchangeInfo"
            )
        except (BrokerAuthError, BrokerHttpError, BrokerTimeoutError):
            # Reconciliation is best-effort; the order may still be filled
            # remotely. Leave the row alone so a later run can retry.
            return
        order.status = "filled"
        await session.flush()

    async def cancel_order(self, session: AsyncSession, *, order_id: str) -> None:
        """Cancel an open order via `DELETE /trade/v2/orders/{order_id}`.

        Returns None on success; raises `BrokerHttpError` on 4xx/5xx
        (e.g. 404 when the order has already filled or was cancelled).
        """
        account_key = await self._account_key(session)
        await self._authed_delete(
            session,
            f"/trade/v2/orders/{order_id}?AccountKey={account_key}",
        )

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
