"""LLM provider abstraction.

Agents depend on `ILlmProvider`, never on a specific SDK. This keeps:
- Tests deterministic (use `FakeLlmProvider`).
- The LLM choice swappable (Ollama → OpenAI → Anthropic) by config change.

Ollama is the MVP default. We call its HTTP API directly via httpx — no third-
party ollama package, fewer dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(slots=True, frozen=True)
class LlmRequest:
    prompt: str
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.2
    max_tokens: int = 1024


@dataclass(slots=True, frozen=True)
class LlmResponse:
    text: str
    parsed: dict[str, Any] | None  # populated when json_schema is provided


class ILlmProvider(Protocol):
    """Generate text or structured JSON from a prompt."""

    async def generate(self, request: LlmRequest) -> LlmResponse:
        ...


# ----------------------------------------------------------------------------
# Ollama
# ----------------------------------------------------------------------------


class OllamaProvider:
    """HTTP client for Ollama's /api/generate endpoint.

    Sends `format: "json"` when a json_schema is provided. Ollama validates only
    that the output is valid JSON, not the schema — we parse + validate after.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate(self, request: LlmRequest) -> LlmResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.json_schema is not None:
            body["format"] = "json"

        response = await self._client.post(f"{self._base_url}/api/generate", json=body)
        response.raise_for_status()
        data = response.json()
        text = data.get("response", "")

        parsed: dict[str, Any] | None = None
        if request.json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"LLM returned invalid JSON: {exc}; raw={text[:200]!r}") from exc

        return LlmResponse(text=text, parsed=parsed)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ----------------------------------------------------------------------------
# FakeLlmProvider — for tests
# ----------------------------------------------------------------------------


class FakeLlmProvider:
    """Returns canned responses for tests.

    Usage:
        provider = FakeLlmProvider()
        provider.enqueue(LlmResponse(text="...", parsed={"signals": [...]}))
        await provider.generate(request)
    """

    def __init__(self) -> None:
        self._queue: list[LlmResponse] = []
        self.calls: list[LlmRequest] = []

    def enqueue(self, response: LlmResponse) -> None:
        self._queue.append(response)

    def enqueue_json(self, parsed: dict[str, Any]) -> None:
        self.enqueue(LlmResponse(text=json.dumps(parsed), parsed=parsed))

    async def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        if not self._queue:
            raise RuntimeError("FakeLlmProvider has no queued responses")
        return self._queue.pop(0)
