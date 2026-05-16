"""Tests for /v1/oauth/saxo/* + /v1/accounts/{id} routes."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.api import (
    broker_factory_dep,
    clock_dep,
    create_app,
    saxo_http_client_dep,
    session_factory_dep,
    settings_dep,
)
from snapd_invest.broker import PaperBroker, SaxoBroker
from snapd_invest.broker.saxo import SAXO_SIM_API_BASE
from snapd_invest.broker.saxo_oauth import (
    SIM_TOKEN_URL,
    TokenSet,
    generate_pkce,
    load_tokens,
    persist_oauth_state,
    store_tokens,
)
from snapd_invest.config import Settings
from snapd_invest.crypto import FernetCipher
from snapd_invest.models import Account, OAuthState
from snapd_invest.portfolio import create_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import IBroker
    from snapd_invest.clock import FakeClock


@pytest.fixture
def sim_settings() -> Settings:
    """Settings with Saxo SIM env vars set."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        saxo_env="sim",
        saxo_client_id="client-123",
        saxo_redirect_uri="http://localhost:8000/v1/oauth/saxo/callback",
        encryption_key=Fernet.generate_key().decode("ascii"),
    )


def _build_test_app(
    *,
    db_engine: object,
    fake_clock: FakeClock,
    settings: Settings,
    saxo_http_client: httpx.AsyncClient | None = None,
):
    """Construct an app wired against test fixtures.

    The lifespan is intentionally NOT driven; tests populate state via
    dependency_overrides instead. This keeps tests fast and free of side
    effects (no scheduler, no real Ollama client).
    """
    app = create_app()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    paper_broker = PaperBroker(fake_clock)
    http_client = saxo_http_client or httpx.AsyncClient()

    cipher = (
        FernetCipher(settings.encryption_key.encode("ascii"))
        if settings.encryption_key is not None
        else None
    )

    def _factory(account: Account) -> IBroker:
        if account.account_type == "paper":
            return paper_broker
        if account.account_type == "sim":
            assert cipher is not None and settings.saxo_client_id is not None
            return SaxoBroker(
                client=http_client,
                clock=fake_clock,
                cipher=cipher,
                client_id=settings.saxo_client_id,
                account_id=account.id,
            )
        raise ValueError(f"unsupported account_type: {account.account_type}")

    app.dependency_overrides[settings_dep] = lambda: settings
    app.dependency_overrides[clock_dep] = lambda: fake_clock
    app.dependency_overrides[session_factory_dep] = lambda: factory
    app.dependency_overrides[saxo_http_client_dep] = lambda: http_client
    app.dependency_overrides[broker_factory_dep] = lambda: _factory
    return app


# ---------------------------------------------------------------------------
# /v1/oauth/saxo/start
# ---------------------------------------------------------------------------


class TestOAuthStart:
    async def test_returns_authorize_url_and_persists_state(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await db_session.commit()

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/v1/oauth/saxo/start?account_id={account.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        url = urlparse(body["authorize_url"])
        assert url.netloc == "sim.logonvalidation.net"
        assert url.path == "/authorize"
        q = parse_qs(url.query)
        assert q["client_id"] == ["client-123"]
        assert q["redirect_uri"] == ["http://localhost:8000/v1/oauth/saxo/callback"]
        assert q["response_type"] == ["code"]
        assert q["code_challenge_method"] == ["S256"]
        assert "code_challenge" in q
        assert "state" in q

        # State persisted (re-read via the test's session)
        await db_session.commit()
        row = (
            await db_session.execute(select(OAuthState).where(OAuthState.state == q["state"][0]))
        ).scalar_one()
        assert row.account_id == account.id
        assert row.broker == "saxo"

    async def test_rejects_unknown_account(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/v1/oauth/saxo/start?account_id=does-not-exist")
        assert resp.status_code == 404

    async def test_requires_saxo_client_id_configured(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await db_session.commit()
        bare_settings = Settings(_env_file=None)  # type: ignore[call-arg]

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=bare_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/v1/oauth/saxo/start?account_id={account.id}")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /v1/oauth/saxo/callback
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    @respx.mock
    async def test_happy_path_persists_tokens(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        pkce = generate_pkce()
        await persist_oauth_state(
            db_session,
            fake_clock,
            account_id=account.id,
            broker="saxo",
            state="state-1",
            code_verifier=pkce.verifier,
        )
        await db_session.commit()

        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "a1",
                    "refresh_token": "r1",
                    "expires_in": 1200,
                    "refresh_token_expires_in": 86400,
                    "token_type": "Bearer",
                },
            )
        )

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/v1/oauth/saxo/callback?code=auth-code&state=state-1")
        assert resp.status_code == 200
        assert "auth complete" in resp.text.lower()

        # Tokens persisted, encrypted
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        await db_session.commit()
        loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
        assert loaded is not None
        assert loaded.access_token == "a1"

    async def test_unknown_state_returns_400(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/v1/oauth/saxo/callback?code=x&state=nonexistent")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /v1/oauth/saxo/status
# ---------------------------------------------------------------------------


class TestOAuthStatus:
    async def test_reports_unauthenticated_when_no_tokens(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await db_session.commit()
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/oauth/saxo/status?account_id={account.id}")
        assert resp.status_code == 200
        assert resp.json() == {
            "account_id": account.id,
            "broker": "saxo",
            "authenticated": False,
        }

    async def test_reports_authenticated_when_tokens_present(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="a",
                refresh_token="r",
                access_expires_at=fake_clock.now() + timedelta(seconds=1200),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )
        await db_session.commit()

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/oauth/saxo/status?account_id={account.id}")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True


# ---------------------------------------------------------------------------
# /v1/accounts/{id}
# ---------------------------------------------------------------------------


class TestGetAccount:
    @respx.mock
    async def test_proxies_through_broker_for_sim_accounts(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="t",
                refresh_token="r",
                access_expires_at=fake_clock.now() + timedelta(seconds=600),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )
        await db_session.commit()

        respx.get(f"{SAXO_SIM_API_BASE}/port/v1/users/me").mock(
            return_value=httpx.Response(
                200, json={"ClientKey": "ck", "UserKey": "uk", "Name": "Torben"}
            )
        )

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/accounts/{account.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_key"] == "ck"
        assert body["name"] == "Torben"
        assert body["account_type"] == "sim"

    async def test_returns_paper_info_without_calling_broker(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper", account_type="paper")
        await db_session.commit()
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/accounts/{account.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["account_type"] == "paper"
        assert body["client_key"] is None

    async def test_returns_404_for_unknown_account(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/v1/accounts/does-not-exist")
        assert resp.status_code == 404

    async def test_returns_401_when_no_tokens_stored_for_sim_account(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        """When the user never completed OAuth, surface a 401 with a
        machine-parseable code so the CLI can tell them to re-run auth."""
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        await db_session.commit()

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/accounts/{account.id}")
        assert resp.status_code == 401, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "saxo_reauth_required"
        assert body["detail"]["account_id"] == account.id
        assert "snapdinvest auth saxo" in body["detail"]["message"]

    @respx.mock
    async def test_returns_401_when_refresh_fails(
        self,
        db_session: AsyncSession,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        """When the stored access token is near expiry and the refresh
        endpoint rejects our refresh token, surface a 401 + reauth code."""
        assert sim_settings.encryption_key is not None
        cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
        account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
        # access_expires_at within REFRESH_BUFFER_SECONDS (=60) so the
        # proactive-refresh path in get_active_access_token triggers.
        await store_tokens(
            db_session,
            fake_clock,
            cipher,
            account_id=account.id,
            broker="saxo",
            tokens=TokenSet(
                access_token="stale",
                refresh_token="expired",
                access_expires_at=fake_clock.now() + timedelta(seconds=30),
                refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
            ),
        )
        await db_session.commit()

        respx.post(SIM_TOKEN_URL).mock(
            return_value=httpx.Response(401, json={"error": "invalid_grant"})
        )

        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/accounts/{account.id}")
        assert resp.status_code == 401, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "saxo_reauth_required"
        assert body["detail"]["account_id"] == account.id


# ---------------------------------------------------------------------------
# POST /v1/accounts
# ---------------------------------------------------------------------------


class TestCreateAccount:
    async def test_creates_paper_account(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/accounts",
                json={
                    "name": "paper-main",
                    "account_type": "paper",
                    "base_currency": "DKK",
                    "initial_cash": "100000",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "paper-main"
        assert body["account_type"] == "paper"
        assert body["saxo_account_id"] is None
        assert len(body["account_id"]) == 36

    async def test_creates_sim_account_with_saxo_id(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/accounts",
                json={
                    "name": "saxo-sim",
                    "account_type": "sim",
                    "saxo_account_id": "22264911",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["account_type"] == "sim"
        assert body["saxo_account_id"] == "22264911"

    async def test_rejects_saxo_fields_on_paper_account(
        self,
        db_engine: object,
        fake_clock: FakeClock,
        sim_settings: Settings,
    ) -> None:
        app = _build_test_app(db_engine=db_engine, fake_clock=fake_clock, settings=sim_settings)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/accounts",
                json={
                    "name": "bad-paper",
                    "account_type": "paper",
                    "saxo_account_id": "22264911",
                },
            )
        assert resp.status_code == 400
