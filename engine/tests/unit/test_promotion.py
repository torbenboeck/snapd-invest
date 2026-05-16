"""Tests for the promotion gate."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from snapd_invest.models import Account
from snapd_invest.promotion import Allowed, DeniedFor, trivial_promotion_gate


def _account(account_type: str) -> Account:
    return Account(
        id="acc-1",
        name="x",
        account_type=account_type,
        base_currency="DKK",
        cash=Decimal("0"),
        created_at=datetime(2026, 5, 16, tzinfo=UTC),
    )


class TestTrivialPromotionGate:
    def test_paper_always_allowed(self) -> None:
        d = trivial_promotion_gate(_account("paper"), broker=None)  # type: ignore[arg-type]
        assert isinstance(d, Allowed)

    def test_sim_allowed_for_now(self) -> None:
        d = trivial_promotion_gate(_account("sim"), broker=None)  # type: ignore[arg-type]
        assert isinstance(d, Allowed)

    def test_live_denied(self) -> None:
        d = trivial_promotion_gate(_account("live"), broker=None)  # type: ignore[arg-type]
        assert isinstance(d, DeniedFor)
        assert "live" in d.reason
