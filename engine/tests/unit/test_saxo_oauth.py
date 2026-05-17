"""Tests for `snapd_invest.broker.saxo_oauth`."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import select

from snapd_invest.broker import BrokerAuthError
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    PkceChallenge,
    TokenSet,
    consume_oauth_state,
    exchange_code_for_tokens,
    generate_pkce,
    get_active_access_token,
    load_tokens,
    persist_oauth_state,
    refresh_tokens,
    store_tokens,
)
from snapd_invest.crypto import FernetCipher
from snapd_invest.models import OAuthToken
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


class TestTokenStore:
    async def test_store_then_load_roundtrip(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        tokens = TokenSet(
            access_token="access-abc",
            refresh_token="refresh-xyz",
            access_expires_at=fake_clock.now() + timedelta(seconds=1200),
            refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
        )

        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=tokens,
        )

        loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
        assert loaded is not None
        assert loaded.access_token == "access-abc"
        assert loaded.refresh_token == "refresh-xyz"
        assert loaded.access_expires_at == tokens.access_expires_at

        # The DB row must contain ciphertext, not plaintext
        row = (
            await db_session.execute(select(OAuthToken).where(OAuthToken.account_id == account.id))
        ).scalar_one()
        assert "access-abc" not in row.access_token_encrypted
        assert "refresh-xyz" not in row.refresh_token_encrypted

    async def test_store_overwrites_existing_tokens_for_same_account_and_broker(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        first = TokenSet(
            "a1",
            "r1",
            fake_clock.now() + timedelta(seconds=600),
            fake_clock.now() + timedelta(seconds=86400),
        )
        second = TokenSet(
            "a2",
            "r2",
            fake_clock.now() + timedelta(seconds=1200),
            fake_clock.now() + timedelta(seconds=86400),
        )

        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=first,
        )
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=second,
        )

        loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
        assert loaded is not None
        assert loaded.access_token == "a2"

    async def test_load_returns_none_when_no_tokens(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        assert await load_tokens(db_session, cipher, account_id=account.id, broker="saxo") is None


class TestRefreshTokens:
    @respx.mock
    async def test_refresh_returns_new_tokens(self, fake_clock: FakeClock) -> None:
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            new = await refresh_tokens(
                client,
                fake_clock,
                client_id="client-123",
                refresh_token="old-refresh",
            )

        assert new.access_token == "new-access"
        assert new.refresh_token == "new-refresh"

    @respx.mock
    async def test_refresh_raises_auth_error_on_failure(self, fake_clock: FakeClock) -> None:
        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(BrokerAuthError, match="invalid_grant"):
                await refresh_tokens(client, fake_clock, client_id="x", refresh_token="bad")


class TestGetActiveAccessToken:
    @respx.mock
    async def test_returns_stored_token_when_not_near_expiry(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="still-good",
                refresh_token="r1",
                access_expires_at=fake_clock.now() + timedelta(seconds=600),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )

        async with httpx.AsyncClient() as client:
            token = await get_active_access_token(
                db_session,
                fake_clock,
                client,
                cipher,
                client_id="client-123",
                account_id=account.id,
                broker="saxo",
            )

        assert token == "still-good"

    @respx.mock
    async def test_refreshes_proactively_when_within_buffer(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="almost-expired",
                refresh_token="r1",
                access_expires_at=fake_clock.now() + timedelta(seconds=30),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )

        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "fresh",
                    "refresh_token": "r2",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            token = await get_active_access_token(
                db_session,
                fake_clock,
                client,
                cipher,
                client_id="client-123",
                account_id=account.id,
                broker="saxo",
            )

        assert token == "fresh"
        loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
        assert loaded is not None
        assert loaded.access_token == "fresh"

    async def test_raises_auth_error_when_no_tokens_stored(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        async with httpx.AsyncClient() as client:
            with pytest.raises(BrokerAuthError, match="no stored tokens"):
                await get_active_access_token(
                    db_session,
                    fake_clock,
                    client,
                    cipher,
                    client_id="client-123",
                    account_id=account.id,
                    broker="saxo",
                )

    @respx.mock
    async def test_concurrent_callers_refresh_only_once(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        """C-02: Saxo refresh tokens are single-use; concurrent callers must
        serialize so only one actually calls /token."""
        cipher = FernetCipher(Fernet.generate_key())
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="almost-expired",
                refresh_token="r1",
                access_expires_at=fake_clock.now() + timedelta(seconds=30),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )

        route = respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "fresh",
                    "refresh_token": "r2",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                get_active_access_token(
                    db_session,
                    fake_clock,
                    client,
                    cipher,
                    client_id="client-123",
                    account_id=account.id,
                    broker="saxo",
                ),
                get_active_access_token(
                    db_session,
                    fake_clock,
                    client,
                    cipher,
                    client_id="client-123",
                    account_id=account.id,
                    broker="saxo",
                ),
            )

        assert route.call_count == 1
        assert results == ["fresh", "fresh"]
