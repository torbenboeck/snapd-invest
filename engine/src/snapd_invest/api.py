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

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from snapd_invest import __version__
from snapd_invest.audit import list_events
from snapd_invest.broker import PaperBroker
from snapd_invest.clock import Clock, SystemClock
from snapd_invest.config import Settings, get_settings
from snapd_invest.data import ensure_instrument
from snapd_invest.llm import OllamaProvider
from snapd_invest.logging_config import configure_logging, get_logger
from snapd_invest.persistence import make_engine, make_session_factory, session_scope
from snapd_invest.pipeline import run_agent_once, run_microtrader_once
from snapd_invest.portfolio import (
    build_summary,
    create_account,
    get_account_by_name,
)
from snapd_invest.recommendation import (
    SignalModification,
    approve_and_execute,
    get_recommendation,
    list_recommendations,
    reject,
)
from snapd_invest.risk import RiskConfig
from snapd_invest.strategy import SMACrossoverStrategy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log: structlog.stdlib.BoundLogger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------


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
    log.info("engine_started", version=__version__, db_path=str(settings.db_path))

    try:
        yield
    finally:
        await app.state.llm.aclose()
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


def broker_dep(request: Request) -> PaperBroker:
    return request.app.state.broker  # type: ignore[no-any-return]


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


# ----------------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------------


def create_app() -> FastAPI:
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
        account_name: str = "paper",
    ) -> PortfolioDto:
        account = await get_account_by_name(session, account_name)
        if account is None:
            account = await create_account(
                session, clock, name=account_name, initial_cash=Decimal("100000")
            )
        summary = await build_summary(session, account)
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
        broker: Annotated[PaperBroker, Depends(broker_dep)],
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
            broker,
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
        broker: Annotated[PaperBroker, Depends(broker_dep)],
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
                broker,
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

    return app


app = create_app()
