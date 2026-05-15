"""Tests for `snapd_invest.broker.saxo_oauth`."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from snapd_invest.broker import BrokerAuthError
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    PkceChallenge,
    TokenSet,
    consume_oauth_state,
    exchange_code_for_tokens,
    generate_pkce,
    persist_oauth_state,
)
from snapd_invest.portfolio import create_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


class TestGeneratePkce:
    def test_verifier_is_43_to_128_url_safe_chars(self) -> None:
        ch = generate_pkce()
        assert isinstance(ch, PkceChallenge)
        assert 43 <= len(ch.verifier) <= 128
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", ch.verifier)

    def test_challenge_is_s256_of_verifier(self) -> None:
        ch = generate_pkce()
        digest = hashlib.sha256(ch.verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert ch.challenge == expected
        assert ch.method == "S256"

    def test_each_call_produces_a_new_verifier(self) -> None:
        assert generate_pkce().verifier != generate_pkce().verifier


class TestOAuthStatePersistence:
    async def test_persist_and_consume_roundtrip(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        ch = generate_pkce()
        await persist_oauth_state(
            db_session,
            fake_clock,
            account_id=account.id,
            broker="saxo",
            state="state-value-xyz",
            code_verifier=ch.verifier,
            ttl=timedelta(minutes=10),
        )

        consumed = await consume_oauth_state(db_session, fake_clock, state="state-value-xyz")
        assert consumed is not None
        assert consumed.account_id == account.id
        assert consumed.code_verifier == ch.verifier

        # consumed once = gone
        assert await consume_oauth_state(db_session, fake_clock, state="state-value-xyz") is None

    async def test_consume_rejects_expired_state(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await persist_oauth_state(
            db_session,
            fake_clock,
            account_id=account.id,
            broker="saxo",
            state="exp",
            code_verifier="v",
            ttl=timedelta(minutes=1),
        )
        fake_clock.advance(hours=1)
        # Expired -> treated as absent
        assert await consume_oauth_state(db_session, fake_clock, state="exp") is None


class TestExchangeCodeForTokens:
    @respx.mock
    async def test_happy_path_returns_tokens(self, fake_clock: FakeClock) -> None:
        route = respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access-abc",
                    "refresh_token": "refresh-xyz",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tokens = await exchange_code_for_tokens(
                client,
                fake_clock,
                client_id="client-123",
                redirect_uri="http://localhost:8000/cb",
                code="auth-code-abc",
                code_verifier="v" * 64,
            )

        assert route.called
        raw_body = route.calls.last.request.read().decode()
        assert "grant_type=authorization_code" in raw_body
        assert "code=auth-code-abc" in raw_body
        assert f"code_verifier={'v' * 64}" in raw_body
        assert "client_id=client-123" in raw_body
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fcb" in raw_body

        assert isinstance(tokens, TokenSet)
        assert tokens.access_token == "access-abc"
        assert tokens.refresh_token == "refresh-xyz"
        assert (tokens.access_expires_at - fake_clock.now()).total_seconds() == 1200
        assert (tokens.refresh_expires_at - fake_clock.now()).total_seconds() == 86400

    @respx.mock
    async def test_raises_auth_error_on_4xx(self, fake_clock: FakeClock) -> None:
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(BrokerAuthError, match="invalid_grant"):
                await exchange_code_for_tokens(
                    client,
                    fake_clock,
                    client_id="x",
                    redirect_uri="http://localhost/cb",
                    code="bad",
                    code_verifier="v" * 64,
                )
