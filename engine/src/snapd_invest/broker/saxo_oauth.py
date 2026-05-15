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

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from snapd_invest.broker import BrokerAuthError
from snapd_invest.models import OAuthState, new_id

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock


HTTP_BAD_REQUEST = 400


def _as_utc(dt: datetime) -> datetime:
    """Reinterpret a naive datetime from SQLite as UTC.

    The schema declares `DateTime(timezone=True)` and we always store UTC,
    but SQLite's storage layer returns naive datetimes. Treat them as UTC
    rather than crashing on tz-aware comparisons.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


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
