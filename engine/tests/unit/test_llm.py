"""Tests for `algo_invest.llm`."""

from __future__ import annotations

import json

import pytest

from algo_invest.llm import FakeLlmProvider, LlmRequest, LlmResponse


class TestFakeLlmProvider:
    async def test_returns_queued_response(self) -> None:
        provider = FakeLlmProvider()
        provider.enqueue(LlmResponse(text="hi", parsed=None))

        result = await provider.generate(LlmRequest(prompt="hello"))
        assert result.text == "hi"

    async def test_raises_when_queue_empty(self) -> None:
        provider = FakeLlmProvider()
        with pytest.raises(RuntimeError, match="no queued"):
            await provider.generate(LlmRequest(prompt="hi"))

    async def test_records_calls(self) -> None:
        provider = FakeLlmProvider()
        provider.enqueue(LlmResponse(text="x", parsed=None))
        await provider.generate(LlmRequest(prompt="probe"))
        assert provider.calls[0].prompt == "probe"

    async def test_enqueue_json_helper(self) -> None:
        provider = FakeLlmProvider()
        provider.enqueue_json({"signals": [], "summary": "neutral"})

        result = await provider.generate(LlmRequest(prompt="hi"))
        assert result.parsed == {"signals": [], "summary": "neutral"}
        assert json.loads(result.text) == result.parsed
