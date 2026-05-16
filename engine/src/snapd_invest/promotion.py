"""Promotion gate — decides whether an account may receive orders.

ADR-003 (promotion gates in code, not custom): each account / strategy /
agent has a promotion configuration. The gate is the enforcement point.
T-001-B ships the trivial implementation; eval-thresholded gates land
later by swapping `PromotionGate` for a different callable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from snapd_invest.broker import IBroker
    from snapd_invest.models import Account


@dataclass(slots=True, frozen=True)
class Allowed:
    """The account may receive orders."""

    kind: Literal["allowed"] = field(default="allowed", init=False)


@dataclass(slots=True, frozen=True)
class DeniedFor:
    """The account may not receive orders; `reason` is logged + surfaced."""

    reason: str
    kind: Literal["denied"] = field(default="denied", init=False)


PromotionDecision = Allowed | DeniedFor
PromotionGate = Callable[["Account", "IBroker"], PromotionDecision]


def trivial_promotion_gate(account: Account, broker: IBroker) -> PromotionDecision:  # noqa: ARG001
    """MVP gate: paper always; sim if account_type==sim (no liveness check yet).

    `broker` is part of the `PromotionGate` Callable signature so eval-
    thresholded gates can perform liveness checks. The trivial gate ignores
    it. Eval-thresholded promotion is a future task that swaps this
    function pointer.
    """
    if account.account_type == "paper":
        return Allowed()
    if account.account_type == "sim":
        return Allowed()
    return DeniedFor(reason=f"unsupported account_type: {account.account_type!r}")
