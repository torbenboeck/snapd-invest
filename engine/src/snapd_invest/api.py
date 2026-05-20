"""FastAPI application.

HTTP wiring only. Business logic lives in `strategy.py`, `agent.py`,
`execution.py`, `recommendation.py`, etc.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (  # noqa: TC002 — runtime needed for FastAPI get_type_hints on Annotated[AsyncSession, Depends(...)]
    AsyncSession,
    async_sessionmaker,
)

from snapd_invest import __version__
from snapd_invest.audit import list_events
from snapd_invest.broker import (
    BrokerAuthError,
    BrokerFactory,
    IBroker,
    PaperBroker,
    SaxoBroker,
)
from snapd_invest.broker.saxo_oauth import (
    SIM_AUTHORIZE_URL,
    backfill_saxo_identity,
    consume_oauth_state,
    exchange_code_for_tokens,
    generate_pkce,
    load_tokens,
    persist_oauth_state,
    store_tokens,
)
from snapd_invest.clock import Clock, SystemClock
from snapd_invest.config import Settings, get_settings
from snapd_invest.crypto import FernetCipher
from snapd_invest.data import ensure_instrument
from snapd_invest.llm import OllamaProvider
from snapd_invest.logging_config import configure_logging, get_logger
from snapd_invest.models import Account
from snapd_invest.persistence import make_engine, make_session_factory, session_scope
from snapd_invest.pipeline import run_agent_once, run_microtrader_once
from snapd_invest.portfolio import (
    build_summary,
    create_account,
    get_account_by_name,
)
from snapd_invest.promotion import PromotionGate, trivial_promotion_gate
from snapd_invest.recommendation import (
    SignalModification,
    approve_and_execute,
    get_recommendation,
    list_recommendations,
    reject,
)
from snapd_invest.risk import RiskConfig
from snapd_invest.scheduler import build_default_jobs, build_scheduler
from snapd_invest.strategy import SMACrossoverStrategy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response

log: structlog.stdlib.BoundLogger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------


def _make_broker_factory(
    settings: Settings,
    clock: Clock,
    paper_broker: PaperBroker,
    saxo_http_client: httpx.AsyncClient,
) -> BrokerFactory:
    """Build the per-account broker factory.

    `paper` accounts get the shared `PaperBroker`. `sim` accounts get a fresh
    `SaxoBroker` constructed with the shared httpx client + cipher derived
    from `SNAPDINVEST_ENCRYPTION_KEY`. `live` is hard-blocked.
    """

    def factory(account: Account) -> IBroker:
        if account.account_type == "paper":
            return paper_broker
        if account.account_type == "sim":
            if settings.saxo_client_id is None or settings.encryption_key is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "SIM account requires SNAPDINVEST_SAXO_CLIENT_ID and "
                        "SNAPDINVEST_ENCRYPTION_KEY"
                    ),
                )
            return SaxoBroker(
                client=saxo_http_client,
                clock=clock,
                cipher=FernetCipher(settings.encryption_key.encode("ascii")),
                client_id=settings.saxo_client_id,
                account_id=account.id,
            )
        raise HTTPException(
            status_code=400, detail=f"unsupported account_type: {account.account_type}"
        )

    return factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    engine = make_engine(settings)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.clock = SystemClock()
    app.state.broker = PaperBroker(app.state.clock)
    app.state.llm = OllamaProvider()
    app.state.risk_config = RiskConfig()
    app.state.scheduler = None
    app.state.saxo_http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    app.state.broker_factory = _make_broker_factory(
        settings, app.state.clock, app.state.broker, app.state.saxo_http_client
    )
    app.state.promotion_gate = trivial_promotion_gate

    if settings.scheduler_enabled:
        jobs = build_default_jobs(
            session_factory=app.state.session_factory,
            clock=app.state.clock,
            broker_factory=app.state.broker_factory,
            promotion_gate=app.state.promotion_gate,
            llm=app.state.llm,
            risk_config=app.state.risk_config,
            settings=settings,
        )
        scheduler = build_scheduler(jobs)
        scheduler.start()
        app.state.scheduler = scheduler
        log.info("scheduler_started", job_count=len(jobs))
    else:
        log.info("scheduler_disabled")

    log.info("engine_started", version=__version__, db_path=str(settings.db_path))

    try:
        yield
    finally:
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=False)
            log.info("scheduler_stopped")
        await app.state.llm.aclose()
        await app.state.saxo_http_client.aclose()
        await engine.dispose()
        log.info("engine_stopped")


# ----------------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return get_settings()


def settings_dep() -> Settings:
    return _cached_settings()


def session_factory_dep(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


def clock_dep(request: Request) -> Clock:
    return request.app.state.clock  # type: ignore[no-any-return]


def saxo_http_client_dep(request: Request) -> httpx.AsyncClient:
    return request.app.state.saxo_http_client  # type: ignore[no-any-return]


def broker_factory_dep(request: Request) -> BrokerFactory:
    return request.app.state.broker_factory  # type: ignore[no-any-return]


def promotion_gate_dep(request: Request) -> PromotionGate:
    return request.app.state.promotion_gate  # type: ignore[no-any-return]


def llm_dep(request: Request) -> OllamaProvider:
    return request.app.state.llm  # type: ignore[no-any-return]


def risk_dep(request: Request) -> RiskConfig:
    return request.app.state.risk_config  # type: ignore[no-any-return]


async def session_dep(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(session_factory_dep)],
) -> AsyncIterator[AsyncSession]:
    async for s in session_scope(factory):
        yield s


# ----------------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------------


async def correlation_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# ----------------------------------------------------------------------------
# DTOs
# ----------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    version: str


class AuditEventDto(BaseModel):
    id: str
    type: str
    payload: str
    correlation_id: str | None
    occurred_at: str


class PositionDto(BaseModel):
    instrument_symbol: str
    instrument_exchange: str
    quantity: Decimal
    avg_cost: Decimal
    last_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    tag: str


class PortfolioDto(BaseModel):
    account_id: str
    account_name: str
    base_currency: str
    cash: Decimal
    equity: Decimal | None
    positions: list[PositionDto]


class SignalDto(BaseModel):
    source: str
    instrument_symbol: str
    instrument_exchange: str
    action: Literal["buy", "sell", "hold"]
    quantity: Decimal
    conviction: Decimal
    rationale: str


class RunOnceResponseDto(BaseModel):
    correlation_id: str
    strategy: str
    signals: list[SignalDto]
    outcomes: list[dict[str, Any]]


class RecommendationDto(BaseModel):
    id: str
    agent_id: str
    status: str
    rationale: str
    signals: str
    correlation_id: str | None
    created_at: str
    expires_at: str
    resolved_at: str | None


class ApproveRequest(BaseModel):
    modifications: list[SignalModificationDto] | None = None


class SignalModificationDto(BaseModel):
    instrument_symbol: str
    instrument_exchange: str
    quantity: Decimal | None = None
    skip: bool = False


class RejectRequest(BaseModel):
    reason: str | None = None


class ApproveResponseDto(BaseModel):
    recommendation_id: str
    status: str
    execution_summaries: list[dict[str, Any]]


ApproveRequest.model_rebuild()


# -- OAuth + accounts --


class AuthorizeUrlResponse(BaseModel):
    authorize_url: str
    state: str


class OAuthStatusResponse(BaseModel):
    account_id: str
    broker: str
    authenticated: bool


class AccountInfoDto(BaseModel):
    account_id: str
    account_type: str
    client_key: str | None = None
    user_key: str | None = None
    name: str | None = None


class CreateAccountRequest(BaseModel):
    name: str
    account_type: Literal["paper", "sim"]
    base_currency: str = "DKK"
    initial_cash: Decimal = Decimal("0")
    saxo_client_key: str | None = None
    saxo_account_key: str | None = None
    saxo_account_id: str | None = None


class CreateAccountResponse(BaseModel):
    account_id: str
    name: str
    account_type: str
    base_currency: str
    saxo_account_id: str | None = None


_CALLBACK_HTML = """<!doctype html>
<html><body style="font-family: system-ui">
<h1>Auth complete</h1>
<p>You can close this tab.</p>
</body></html>"""


# ----------------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------------


def create_app() -> FastAPI:  # noqa: PLR0915 — route registrations live here intentionally
    app = FastAPI(
        title="snapd-invest engine",
        description="Hybrid agentic trading engine. Paper-only at MVP.",
        version=__version__,
        lifespan=lifespan,
    )
    app.middleware("http")(correlation_middleware)

    # -- Meta --

    @app.get("/v1/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    # -- Audit --

    @app.get("/v1/audit", response_model=list[AuditEventDto], tags=["audit"])
    async def get_audit(
        session: Annotated[AsyncSession, Depends(session_dep)],
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[AuditEventDto]:
        events = await list_events(session, event_type=event_type, limit=limit)
        return [
            AuditEventDto(
                id=e.id,
                type=e.type,
                payload=e.payload,
                correlation_id=e.correlation_id,
                occurred_at=e.occurred_at.isoformat(),
            )
            for e in events
        ]

    # -- Portfolio --

    @app.get("/v1/portfolio", response_model=PortfolioDto, tags=["portfolio"])
    async def get_portfolio(
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        broker_factory: Annotated[BrokerFactory, Depends(broker_factory_dep)],
        account_name: str = "paper",
    ) -> PortfolioDto:
        account = await get_account_by_name(session, account_name)
        if account is None:
            account = await create_account(
                session, clock, name=account_name, initial_cash=Decimal("100000")
            )
        saxo_broker: SaxoBroker | None = None
        if account.account_type == "sim":
            broker = broker_factory(account)
            if isinstance(broker, SaxoBroker):
                saxo_broker = broker
        try:
            summary = await build_summary(session, account, broker=saxo_broker, clock=clock)
        except BrokerAuthError as exc:
            log.info("saxo_reauth_required", account_id=account.id, reason=str(exc))
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "saxo_reauth_required",
                    "message": (
                        "Saxo session expired or never authenticated; "
                        "run 'snapdinvest auth saxo --account <id>'"
                    ),
                    "account_id": account.id,
                },
            ) from exc
        return PortfolioDto(
            account_id=summary.account_id,
            account_name=summary.account_name,
            base_currency=summary.base_currency,
            cash=summary.cash,
            equity=summary.equity,
            positions=[
                PositionDto(
                    instrument_symbol=p.instrument_symbol,
                    instrument_exchange=p.instrument_exchange,
                    quantity=p.quantity,
                    avg_cost=p.avg_cost,
                    last_price=p.last_price,
                    market_value=p.market_value,
                    unrealized_pnl=p.unrealized_pnl,
                    tag=p.tag,
                )
                for p in summary.positions
            ],
        )

    # -- MicroTrader: run-once --

    @app.post("/v1/run-once", response_model=RunOnceResponseDto, tags=["microtrader"])
    async def run_once(
        request: Request,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        broker_factory: Annotated[BrokerFactory, Depends(broker_factory_dep)],
        promotion_gate: Annotated[PromotionGate, Depends(promotion_gate_dep)],
        risk_config: Annotated[RiskConfig, Depends(risk_dep)],
        instrument_symbol: str = "AAPL",
        instrument_exchange: str = "NASDAQ",
    ) -> RunOnceResponseDto:
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        account = await get_account_by_name(session, "paper")
        if account is None:
            account = await create_account(
                session, clock, name="paper", initial_cash=Decimal("100000")
            )
        instrument = await ensure_instrument(
            session,
            symbol=instrument_symbol,
            exchange=instrument_exchange,
            instrument_type="stock",
            currency="USD",
        )
        outcome = await run_microtrader_once(
            session,
            clock,
            broker_factory,
            promotion_gate,
            risk_config,
            account=account,
            instrument=instrument,
            correlation_id=correlation_id,
        )
        return RunOnceResponseDto(
            correlation_id=correlation_id,
            strategy=SMACrossoverStrategy.name,
            signals=[
                SignalDto(
                    source=s.source,
                    instrument_symbol=s.instrument_symbol,
                    instrument_exchange=s.instrument_exchange,
                    action=s.action,
                    quantity=s.quantity,
                    conviction=s.conviction,
                    rationale=s.rationale,
                )
                for s in outcome.signals
            ],
            outcomes=outcome.execution_summaries,
        )

    # -- Agent: run-once --

    @app.post("/v1/agents/run", tags=["agent"])
    async def run_agent_route(
        request: Request,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        llm: Annotated[OllamaProvider, Depends(llm_dep)],
        instrument_symbol: str = "AAPL",
        instrument_exchange: str = "NASDAQ",
    ) -> dict[str, Any]:
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        account = await get_account_by_name(session, "paper")
        if account is None:
            account = await create_account(
                session, clock, name="paper", initial_cash=Decimal("100000")
            )
        instrument = await ensure_instrument(
            session,
            symbol=instrument_symbol,
            exchange=instrument_exchange,
            instrument_type="stock",
            currency="USD",
        )
        outcome = await run_agent_once(
            session,
            clock,
            llm,
            account=account,
            instrument=instrument,
            correlation_id=correlation_id,
        )
        return {
            "correlation_id": correlation_id,
            "agent": outcome.agent_name,
            "summary": outcome.summary,
            "recommendation_id": outcome.recommendation_id,
        }

    # -- Recommendations --

    @app.get("/v1/recommendations", response_model=list[RecommendationDto], tags=["agent"])
    async def get_recommendations(
        session: Annotated[AsyncSession, Depends(session_dep)],
        status: str | None = None,
        limit: int = 100,
    ) -> list[RecommendationDto]:
        rows = await list_recommendations(session, status=status, limit=limit)
        return [
            RecommendationDto(
                id=r.id,
                agent_id=r.agent_id,
                status=r.status,
                rationale=r.rationale,
                signals=r.signals,
                correlation_id=r.correlation_id,
                created_at=r.created_at.isoformat(),
                expires_at=r.expires_at.isoformat(),
                resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
            )
            for r in rows
        ]

    @app.post(
        "/v1/recommendations/{rec_id}/approve",
        response_model=ApproveResponseDto,
        tags=["agent"],
    )
    async def approve_recommendation(
        rec_id: str,
        payload: ApproveRequest | None,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        broker_factory: Annotated[BrokerFactory, Depends(broker_factory_dep)],
        promotion_gate: Annotated[PromotionGate, Depends(promotion_gate_dep)],
        risk_config: Annotated[RiskConfig, Depends(risk_dep)],
    ) -> ApproveResponseDto:
        rec = await get_recommendation(session, rec_id)
        if rec is None:
            raise HTTPException(404, detail=f"recommendation {rec_id} not found")
        modifications: list[SignalModification] = []
        if payload and payload.modifications:
            modifications = [
                SignalModification(
                    instrument_symbol=m.instrument_symbol,
                    instrument_exchange=m.instrument_exchange,
                    quantity=m.quantity,
                    skip=m.skip,
                )
                for m in payload.modifications
            ]
        try:
            outcome = await approve_and_execute(
                session,
                clock,
                broker_factory,
                promotion_gate,
                risk_config,
                recommendation=rec,
                modifications=modifications,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return ApproveResponseDto(
            recommendation_id=outcome.recommendation.id,
            status=outcome.recommendation.status,
            execution_summaries=outcome.execution_summaries,
        )

    @app.post("/v1/recommendations/{rec_id}/reject", tags=["agent"])
    async def reject_recommendation(
        rec_id: str,
        payload: RejectRequest | None,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
    ) -> dict[str, Any]:
        rec = await get_recommendation(session, rec_id)
        if rec is None:
            raise HTTPException(404, detail=f"recommendation {rec_id} not found")
        try:
            await reject(
                session, clock, recommendation=rec, reason=payload.reason if payload else None
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"recommendation_id": rec.id, "status": rec.status}

    # -- OAuth: Saxo --

    @app.post("/v1/oauth/saxo/start", response_model=AuthorizeUrlResponse, tags=["oauth"])
    async def start_saxo_oauth(
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        settings: Annotated[Settings, Depends(settings_dep)],
        account_id: Annotated[str, Query()],
    ) -> AuthorizeUrlResponse:
        if settings.saxo_client_id is None or settings.saxo_redirect_uri is None:
            raise HTTPException(
                status_code=503,
                detail="SAXO_CLIENT_ID/SAXO_REDIRECT_URI not configured",
            )

        account = (
            await session.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail=f"account_id={account_id} not found")

        pkce = generate_pkce()
        state = uuid.uuid4().hex
        await persist_oauth_state(
            session,
            clock,
            account_id=account.id,
            broker="saxo",
            state=state,
            code_verifier=pkce.verifier,
        )

        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.saxo_client_id,
                "redirect_uri": settings.saxo_redirect_uri,
                "state": state,
                "code_challenge": pkce.challenge,
                "code_challenge_method": "S256",
            }
        )
        return AuthorizeUrlResponse(authorize_url=f"{SIM_AUTHORIZE_URL}?{query}", state=state)

    @app.get("/v1/oauth/saxo/callback", response_class=HTMLResponse, tags=["oauth"])
    async def saxo_oauth_callback(
        code: str,
        state: str,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        settings: Annotated[Settings, Depends(settings_dep)],
        saxo_http_client: Annotated[httpx.AsyncClient, Depends(saxo_http_client_dep)],
    ) -> HTMLResponse:
        if (
            settings.saxo_client_id is None
            or settings.saxo_redirect_uri is None
            or settings.encryption_key is None
        ):
            raise HTTPException(status_code=503, detail="saxo configuration incomplete")

        consumed = await consume_oauth_state(session, clock, state=state)
        if consumed is None:
            raise HTTPException(status_code=400, detail="unknown or expired state")

        tokens = await exchange_code_for_tokens(
            saxo_http_client,
            clock,
            client_id=settings.saxo_client_id,
            redirect_uri=settings.saxo_redirect_uri,
            code=code,
            code_verifier=consumed.code_verifier,
        )
        cipher = FernetCipher(settings.encryption_key.encode("ascii"))
        await store_tokens(
            session,
            clock,
            cipher,
            account_id=consumed.account_id,
            broker=consumed.broker,
            tokens=tokens,
        )

        # Best-effort identity backfill. If Saxo's /clients/me or /accounts/me
        # is unavailable right now, persist tokens anyway — the user can
        # re-authenticate later and we'll retry. Identity is required for
        # placement (T-001-B) but not for the callback itself succeeding.
        account = (
            await session.execute(select(Account).where(Account.id == consumed.account_id))
        ).scalar_one_or_none()
        if account is not None:
            try:
                await backfill_saxo_identity(
                    session,
                    saxo_http_client,
                    access_token=tokens.access_token,
                    account=account,
                )
            except BrokerAuthError as exc:
                log.warning(
                    "saxo_identity_backfill_failed",
                    account_id=account.id,
                    reason=str(exc),
                )
        return HTMLResponse(_CALLBACK_HTML)

    @app.get("/v1/oauth/saxo/status", response_model=OAuthStatusResponse, tags=["oauth"])
    async def saxo_oauth_status(
        account_id: str,
        session: Annotated[AsyncSession, Depends(session_dep)],
        settings: Annotated[Settings, Depends(settings_dep)],
    ) -> OAuthStatusResponse:
        if settings.encryption_key is None:
            return OAuthStatusResponse(account_id=account_id, broker="saxo", authenticated=False)

        cipher = FernetCipher(settings.encryption_key.encode("ascii"))
        loaded = await load_tokens(session, cipher, account_id=account_id, broker="saxo")
        return OAuthStatusResponse(
            account_id=account_id,
            broker="saxo",
            authenticated=loaded is not None,
        )

    @app.post("/v1/accounts", response_model=CreateAccountResponse, tags=["accounts"])
    async def create_account_route(
        payload: CreateAccountRequest,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
    ) -> CreateAccountResponse:
        try:
            account = await create_account(
                session,
                clock,
                name=payload.name,
                account_type=payload.account_type,
                base_currency=payload.base_currency,
                initial_cash=payload.initial_cash,
                saxo_client_key=payload.saxo_client_key,
                saxo_account_key=payload.saxo_account_key,
                saxo_account_id=payload.saxo_account_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return CreateAccountResponse(
            account_id=account.id,
            name=account.name,
            account_type=account.account_type,
            base_currency=account.base_currency,
            saxo_account_id=account.saxo_account_id,
        )

    @app.get("/v1/accounts/{account_id}", response_model=AccountInfoDto, tags=["accounts"])
    async def get_account_info(
        account_id: str,
        session: Annotated[AsyncSession, Depends(session_dep)],
        broker_factory: Annotated[BrokerFactory, Depends(broker_factory_dep)],
    ) -> AccountInfoDto:
        account = (
            await session.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail="account not found")

        broker = broker_factory(account)
        if isinstance(broker, SaxoBroker):
            try:
                info = await broker.get_account(session)
            except BrokerAuthError as exc:
                # Surface as a structured 401 so the CLI can guide the user
                # back through `snapdinvest auth saxo` instead of dumping a
                # stack trace. Saxo SIM refresh tokens expire fast — see
                # docs/integrations/saxo-openapi-notes.md.
                log.info(
                    "saxo_reauth_required",
                    account_id=account.id,
                    reason=str(exc),
                )
                raise HTTPException(
                    status_code=401,
                    detail={
                        "code": "saxo_reauth_required",
                        "message": (
                            "Saxo session expired or never authenticated; "
                            "run 'snapdinvest auth saxo --account <id>'"
                        ),
                        "account_id": account.id,
                    },
                ) from exc
            return AccountInfoDto(
                account_id=account.id,
                account_type=account.account_type,
                client_key=info.client_key,
                user_key=info.user_key,
                name=info.name,
            )
        return AccountInfoDto(account_id=account.id, account_type=account.account_type)

    return app


app = create_app()
