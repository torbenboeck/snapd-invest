"""Tests for the OrderResult discriminated union."""

from __future__ import annotations

from decimal import Decimal
from typing import assert_never

from snapd_invest.broker import (
    BrokerDown,
    Filled,
    IdempotentReplay,
    OrderResult,
    PartiallyFilled,
    Rejected,
)


class TestVariants:
    def test_filled_kind(self) -> None:
        r = Filled(order=...)  # type: ignore[arg-type]
        assert r.kind == "filled"

    def test_rejected_carries_reason(self) -> None:
        r = Rejected(reason="market closed", saxo_error_code="MarketClosed")
        assert r.reason == "market closed"
        assert r.saxo_error_code == "MarketClosed"

    def test_broker_down_carries_detail(self) -> None:
        r = BrokerDown(detail="timeout after 30s")
        assert r.detail == "timeout after 30s"


class TestExhaustiveMatch:
    def test_match_covers_every_variant(self) -> None:
        """Pattern match with assert_never enforces exhaustiveness."""
        variants: list[OrderResult] = [
            Filled(order=...),  # type: ignore[arg-type]
            PartiallyFilled(
                order=...,  # type: ignore[arg-type]
                trades=[],
                remaining_quantity=Decimal("5"),
            ),
            Rejected(reason="x", saxo_error_code=None),
            BrokerDown(detail="x"),
            IdempotentReplay(
                order=...,  # type: ignore[arg-type]
                trades=[],
                original_idempotency_key="k",
            ),
        ]
        for r in variants:
            match r:
                case Filled():
                    label = "filled"
                case PartiallyFilled():
                    label = "partial"
                case Rejected():
                    label = "rejected"
                case BrokerDown():
                    label = "down"
                case IdempotentReplay():
                    label = "replay"
                case _:
                    assert_never(r)
            assert label
