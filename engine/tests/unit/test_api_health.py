"""Tests for the /v1/health endpoint and lifespan wiring."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

from snapd_invest import __version__
from snapd_invest.api import create_app, lifespan

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestHealth:
    async def test_returns_ok(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__

    async def test_correlation_id_echoed(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/health", headers={"X-Correlation-Id": "test-corr-123"})

        assert response.headers["X-Correlation-Id"] == "test-corr-123"

    async def test_correlation_id_generated_if_absent(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/health")

        assert "X-Correlation-Id" in response.headers
        assert len(response.headers["X-Correlation-Id"]) > 10


class TestSchedulerLifespan:
    """ASGITransport doesn't drive the ASGI lifespan protocol, and we don't
    want to add asgi-lifespan as a dep just for two tests. Invoking the
    lifespan async context manager directly tests the same thing — that the
    startup/shutdown side effects on app.state happen as configured."""

    async def test_scheduler_starts_and_stops_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SNAPDINVEST_DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "true")
        app = create_app()
        async with lifespan(app):
            assert app.state.scheduler is not None
            assert app.state.scheduler.running is True

        # AsyncIOScheduler.shutdown(wait=False) defers the STATE_STOPPED
        # transition to the next event-loop tick — yield once so the
        # scheduler's pending callbacks run before we assert.
        await asyncio.sleep(0)
        assert app.state.scheduler.running is False

    async def test_scheduler_skipped_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SNAPDINVEST_DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "false")
        app = create_app()
        async with lifespan(app):
            assert app.state.scheduler is None
