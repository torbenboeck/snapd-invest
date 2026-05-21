"""Saxo OAuth — PKCE state machine, token exchange, refresh, persistence.

Public surface used by `saxo.py` and the FastAPI routes in `api.py`:

- `generate_pkce()` — fresh verifier/challenge pair.
- `persist_oauth_state(...)` / `consume_oauth_state(...)` — the
  server-side state store for in-flight handshakes.
- `exchange_code_for_tokens(...)` — POSTs to Saxo's /token, encrypts + persists.
- `get_active_access_token(...)` — returns a valid access token, refreshing
  proactively if it expires within 60 seconds.
- Endpoint constants: `SIM_AUTHORIZE_URL`, `SIM_TOKEN_URL`.

All tokens are encrypted at rest via `Cipher`. Plaintext never persists.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from snapd_invest.broker import BrokerAuthError
from snapd_invest.models import OAuthState, OAuthToken, new_id

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock
    from snapd_invest.crypto import Cipher
    from snapd_invest.models import Account


HTTP_BAD_REQUEST = 400

# OpenAPI base — duplicated from `broker/saxo.py` to avoid a circular import
# (saxo.py imports from saxo_oauth, not vice versa). The value is stable across
# Saxo deployments; if it ever moves, update both constants.
SIM_OPENAPI_BASE = "https://gateway.saxobank.com/sim/openapi"


def _as_utc(dt: datetime) -> datetime:
    """Reinterpret a naive datetime from SQLite as UTC, normalize tz-aware to UTC.

    The schema declares `DateTime(timezone=True)` and we always store UTC, but
    SQLite's storage layer returns naive datetimes. Treat naive as UTC, and
    convert any aware datetime to UTC so arithmetic with `clock.now()`
    (always UTC) is correct regardless of the producer's tz.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# Per (account_id, broker) lock that serializes token-refresh attempts. Saxo's
# refresh tokens are single-use: two concurrent refreshes would both load the
# same refresh_token, but only the first would succeed; the second receives a
# 400. Single-process scope only — multi-worker deployments need a row-level
# DB lock or external coordinator.
_REFRESH_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _refresh_lock_for(account_id: str, broker: str) -> asyncio.Lock:
    key = (account_id, broker)
    lock = _REFRESH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[key] = lock
    return lock


SIM_AUTHORIZE_URL = "https://sim.logonvalidation.net/authorize"
SIM_TOKEN_URL = "https://sim.logonvalidation.net/token"  # noqa: S105 — endpoint URL, not a secret


@dataclass(slots=True, frozen=True)
class PkceChallenge:
    verifier: str
    challenge: str
    method: str = "S256"


def generate_pkce() -> PkceChallenge:
    """Generate a verifier (RFC 7636 §4.1) and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)  # 64 bytes -> 86 chars; within [43, 128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkceChallenge(verifier=verifier, challenge=challenge)


async def persist_oauth_state(
    session: AsyncSession,
    clock: Clock,
    *,
    account_id: str,
    broker: str,
    state: str,
    code_verifier: str,
    ttl: timedelta = timedelta(minutes=10),
) -> None:
    """Persist an in-flight handshake. The `state` must be unique."""
    now = clock.now()
    row = OAuthState(
        id=new_id(),
        account_id=account_id,
        broker=broker,
        state=state,
        code_verifier=code_verifier,
        created_at=now,
        expires_at=now + ttl,
    )
    session.add(row)
    await session.flush()


async def consume_oauth_state(
    session: AsyncSession,
    clock: Clock,
    *,
    state: str,
) -> OAuthState | None:
    """Look up an OAuthState by `state` and delete it.

    Returns the row if found and unexpired; otherwise `None`. Single-use:
    a second call with the same state returns `None`. The row is consumed
    (deleted) regardless of whether it was expired — exhausting the state
    is part of the CSRF defence.
    """
    row = (
        await session.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalar_one_or_none()
    if row is None:
        return None
    expired = _as_utc(row.expires_at) < clock.now()
    await session.delete(row)
    await session.flush()
    if expired:
        return None
    return row


# ----------------------------------------------------------------------------
# Token exchange
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


async def exchange_code_for_tokens(
    client: httpx.AsyncClient,
    clock: Clock,
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> TokenSet:
    """Exchange an authorization code for an access + refresh token pair.

    Raises `BrokerAuthError` on any non-2xx response from Saxo.
    """
    response = await client.post(
        SIM_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code >= HTTP_BAD_REQUEST:
        raise BrokerAuthError(
            f"saxo token exchange failed: {response.status_code} {response.text[:200]}"
        )
    payload = response.json()
    now = clock.now()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        access_expires_at=now + timedelta(seconds=int(payload["expires_in"])),
        refresh_expires_at=now + timedelta(seconds=int(payload["refresh_token_expires_in"])),
    )


# ----------------------------------------------------------------------------
# Token persistence (encrypted at rest)
# ----------------------------------------------------------------------------


async def store_tokens(
    session: AsyncSession,
    clock: Clock,
    cipher: Cipher,
    *,
    account_id: str,
    broker: str,
    tokens: TokenSet,
) -> None:
    """Upsert encrypted tokens for `(account_id, broker)`."""
    now = clock.now()
    existing = (
        await session.execute(
            select(OAuthToken).where(
                OAuthToken.account_id == account_id,
                OAuthToken.broker == broker,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        row = OAuthToken(
            id=new_id(),
            account_id=account_id,
            broker=broker,
            access_token_encrypted=cipher.encrypt(tokens.access_token),
            refresh_token_encrypted=cipher.encrypt(tokens.refresh_token),
            access_expires_at=tokens.access_expires_at,
            refresh_expires_at=tokens.refresh_expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        existing.access_token_encrypted = cipher.encrypt(tokens.access_token)
        existing.refresh_token_encrypted = cipher.encrypt(tokens.refresh_token)
        existing.access_expires_at = tokens.access_expires_at
        existing.refresh_expires_at = tokens.refresh_expires_at
        existing.updated_at = now
    await session.flush()


async def load_tokens(
    session: AsyncSession,
    cipher: Cipher,
    *,
    account_id: str,
    broker: str,
) -> TokenSet | None:
    """Load and decrypt tokens for `(account_id, broker)`, or `None`."""
    row = (
        await session.execute(
            select(OAuthToken).where(
                OAuthToken.account_id == account_id,
                OAuthToken.broker == broker,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return TokenSet(
        access_token=cipher.decrypt(row.access_token_encrypted),
        refresh_token=cipher.decrypt(row.refresh_token_encrypted),
        access_expires_at=_as_utc(row.access_expires_at),
        refresh_expires_at=_as_utc(row.refresh_expires_at),
    )


# ----------------------------------------------------------------------------
# Refresh
# ----------------------------------------------------------------------------

REFRESH_BUFFER_SECONDS = 60


async def refresh_tokens(
    client: httpx.AsyncClient,
    clock: Clock,
    *,
    client_id: str,
    refresh_token: str,
) -> TokenSet:
    """Use a refresh token to obtain a fresh `TokenSet`.

    Raises `BrokerAuthError` on any non-2xx response from Saxo.
    """
    response = await client.post(
        SIM_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code >= HTTP_BAD_REQUEST:
        raise BrokerAuthError(
            f"saxo token refresh failed: {response.status_code} {response.text[:200]}"
        )
    payload = response.json()
    now = clock.now()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        access_expires_at=now + timedelta(seconds=int(payload["expires_in"])),
        refresh_expires_at=now + timedelta(seconds=int(payload["refresh_token_expires_in"])),
    )


# ----------------------------------------------------------------------------
# Identity backfill (post-OAuth, populates Account.saxo_* columns)
# ----------------------------------------------------------------------------


async def fetch_client_info(client: httpx.AsyncClient, *, access_token: str) -> dict[str, Any]:
    """GET /port/v1/clients/me — ClientKey, ClientId, DefaultAccountId, etc."""
    response = await client.get(
        f"{SIM_OPENAPI_BASE}/port/v1/clients/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code >= HTTP_BAD_REQUEST:
        raise BrokerAuthError(
            f"saxo /clients/me failed: {response.status_code} {response.text[:200]}"
        )
    payload: dict[str, Any] = response.json()
    return payload


async def fetch_accounts_info(client: httpx.AsyncClient, *, access_token: str) -> dict[str, Any]:
    """GET /port/v1/accounts/me — list of accounts under the client."""
    response = await client.get(
        f"{SIM_OPENAPI_BASE}/port/v1/accounts/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code >= HTTP_BAD_REQUEST:
        raise BrokerAuthError(
            f"saxo /accounts/me failed: {response.status_code} {response.text[:200]}"
        )
    payload: dict[str, Any] = response.json()
    return payload


async def backfill_saxo_identity(
    session: AsyncSession,
    client: httpx.AsyncClient,
    *,
    access_token: str,
    account: Account,
) -> None:
    """Populate `account.saxo_client_key` + `.saxo_account_key` from Saxo.

    Strategy: ClientKey from `/clients/me`. AccountKey by matching the row's
    `saxo_account_id` (user-supplied at create-account time) against
    `/accounts/me`. Falls back to the Client's `DefaultAccountId` when the
    account row didn't carry one. Leaves `saxo_account_key` null if no match
    — the caller logs an audit event; user can re-auth or correct the id.
    """
    client_info = await fetch_client_info(client, access_token=access_token)
    accounts_info = await fetch_accounts_info(client, access_token=access_token)

    account.saxo_client_key = client_info.get("ClientKey")

    preferred_id = account.saxo_account_id or client_info.get("DefaultAccountId")
    accounts_data = accounts_info.get("Data", [])
    match = next(
        (a for a in accounts_data if a.get("AccountId") == preferred_id),
        None,
    )
    if match is not None:
        account.saxo_account_key = match.get("AccountKey")
        account.saxo_account_id = match.get("AccountId")
    await session.flush()


async def get_active_access_token(
    session: AsyncSession,
    clock: Clock,
    client: httpx.AsyncClient,
    cipher: Cipher,
    *,
    client_id: str,
    account_id: str,
    broker: str,
) -> str:
    """Return a usable access token for `(account_id, broker)`.

    Refreshes proactively if the stored token expires within
    `REFRESH_BUFFER_SECONDS`. A per-account lock serializes refreshes so a
    second caller in the same window sees the already-refreshed token instead
    of attempting a second refresh (Saxo refresh tokens are single-use).
    Raises `BrokerAuthError` if no tokens are stored, or if refresh fails.
    """
    stored = await load_tokens(session, cipher, account_id=account_id, broker=broker)
    if stored is None:
        raise BrokerAuthError(
            f"no stored tokens for account_id={account_id} broker={broker}; "
            f"complete OAuth first via /v1/oauth/saxo/start"
        )

    if (stored.access_expires_at - clock.now()).total_seconds() > REFRESH_BUFFER_SECONDS:
        return stored.access_token

    async with _refresh_lock_for(account_id, broker):
        # Double-check inside the lock: if another caller refreshed while we
        # were waiting, use that token instead of refreshing again.
        stored = await load_tokens(session, cipher, account_id=account_id, broker=broker)
        if stored is None:
            raise BrokerAuthError(
                f"no stored tokens for account_id={account_id} broker={broker}; "
                f"complete OAuth first via /v1/oauth/saxo/start"
            )
        if (stored.access_expires_at - clock.now()).total_seconds() > REFRESH_BUFFER_SECONDS:
            return stored.access_token

        fresh = await refresh_tokens(
            client, clock, client_id=client_id, refresh_token=stored.refresh_token
        )
        await store_tokens(
            session,
            clock,
            cipher,
            account_id=account_id,
            broker=broker,
            tokens=fresh,
        )
        return fresh.access_token
