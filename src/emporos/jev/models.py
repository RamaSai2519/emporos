"""What crosses the Jev boundary: structured request context in, a machine-readable decision
out. Both are plain, jev-owned value objects — this package never imports `emporos.opportunity`,
so a caller there builds a `JevRequest` from whatever it holds; jev has no opinion on where its
input comes from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

# Jev's operating modes (EM-161), as plain strings rather than a StrEnum: `JevConfig` is what
# validates which of these this build supports, so a config typo fails loudly there instead of
# here.
CONFIRMATION = "confirmation"
RANKING = "ranking"
STRATEGY_SELECTION = "strategy_selection"


@dataclass(frozen=True)
class JevRequest:
    """Structured market/opportunity context for one candidate (EM-152: "Jev should consume
    structured market/opportunity context wherever practical")."""

    symbol: str
    timeframe: str
    regime: str | None
    strategy_name: str
    direction: str
    entry: Decimal
    stop: Decimal
    target: Decimal
    expected_edge: Decimal
    confidence: Decimal
    features: Mapping[str, Decimal] = field(default_factory=dict)
    historical_conditional_performance: Mapping[str, Decimal] = field(default_factory=dict)
    portfolio_context: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbol or not self.strategy_name:
            raise ValueError("a Jev request needs a symbol and a strategy name")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        object.__setattr__(
            self,
            "historical_conditional_performance",
            MappingProxyType(dict(self.historical_conditional_performance)),
        )
        object.__setattr__(
            self, "portfolio_context", MappingProxyType(dict(self.portfolio_context))
        )


CONFIRM = "confirm"
REJECT = "reject"
ABSTAIN = "abstain"  # Jev had no opinion; the caller's fail-open/fail-closed policy decides


@dataclass(frozen=True)
class JevDecision:
    """What Jev returned for one request, or what a failure/timeout is represented as — a
    decision object always exists; callers never branch on "was there a decision", only on
    `decision`, `error` and `confidence`."""

    decision: str  # one of CONFIRM / REJECT / ABSTAIN, or an unrecognized value treated as ABSTAIN
    confidence: Decimal | None
    provider: str
    model: str | None
    config_version: str | None
    requested_at: datetime
    latency_ms: int
    error: str | None = None
    tokens_used: int | None = None  # cost proxy: the gateway bills per token, not per request

    def __post_init__(self) -> None:
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must be timezone-aware")
        if self.confidence is not None and not (Decimal(0) <= self.confidence <= Decimal(1)):
            raise ValueError("confidence must be between 0 and 1")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if self.tokens_used is not None and self.tokens_used < 0:
            raise ValueError("tokens_used cannot be negative")

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def is_confirm(self) -> bool:
        return self.ok and self.decision == CONFIRM


def failed_decision(
    provider: str, *, error: str, requested_at: datetime, latency_ms: int = 0
) -> JevDecision:
    """The shape every failure/timeout is represented as: ABSTAIN with the error recorded, never
    an exception a caller must remember to catch."""
    return JevDecision(
        decision=ABSTAIN,
        confidence=None,
        provider=provider,
        model=None,
        config_version=None,
        requested_at=requested_at,
        latency_ms=latency_ms,
        error=error,
    )
