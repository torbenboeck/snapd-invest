"""Tests for the /v1/health endpoint."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from algo_invest import __version__
from algo_invest.api import create_app


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
            response = await client.get(
                "/v1/health", headers={"X-Correlation-Id": "test-corr-123"}
            )

        assert response.headers["X-Correlation-Id"] == "test-corr-123"

    async def test_correlation_id_generated_if_absent(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/health")

        assert "X-Correlation-Id" in response.headers
        assert len(response.headers["X-Correlation-Id"]) > 10
