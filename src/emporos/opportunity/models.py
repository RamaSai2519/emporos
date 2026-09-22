"""The opportunity record (EM-152 / EM-156): `symbol, strategy, direction, entry, stop, target,
expected_edge, confidence, regime, risk`.

A candidate is built from a strategy's `Signal` plus the `RiskSettings` its config declared
(`stop_loss_pct`, `target_pct`) — never from a broker or a wish; it exists so different
strategies' outputs become *comparable* before portfolio allocation ever sees them. It is not a
`Signal` replacement: the underlying signal is kept verbatim, since that is what execution will
eventually act on if this candidate is selected and passes risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal
from emporos.strategies.regime import MarketRegime


@dataclass(frozen=True)
class RejectedCandidate:
    """A signal that did not become a candidate, and why — "no trade" must stay auditable."""

    strategy_name: str
    instrument_id: str
    reason: str
    ts: datetime

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("a rejection must say why")


@dataclass(frozen=True)
class OpportunityCandidate:
    """One ranked, comparable trade opportunity from one strategy on one instrument."""

    strategy_name: str
    instrument_id: str
    timeframe: Timeframe
    signal: Signal
    entry: Money
    stop: Money
    target: Money
    regime: MarketRegime | None
    generated_at: datetime
    confidence: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        if not (Decimal(0) <= self.confidence <= Decimal(1)):
            raise ValueError("confidence must be between 0 and 1")
        if self.entry <= Money.zero() or self.stop <= Money.zero() or self.target <= Money.zero():
            raise ValueError("entry, stop and target must be positive prices")
        self._require_stop_and_target_on_the_correct_side()

    def _require_stop_and_target_on_the_correct_side(self) -> None:
        if self.signal.side is OrderSide.BUY:
            if not (self.stop < self.entry < self.target):
                raise ValueError("a long candidate needs stop < entry < target")
        else:
            if not (self.target < self.entry < self.stop):
                raise ValueError("a short candidate needs target < entry < stop")

    @property
    def direction(self) -> OrderSide:
        return self.signal.side

    @property
    def risk_per_share(self) -> Money:
        return Money(abs(self.entry.amount - self.stop.amount))

    @property
    def reward_per_share(self) -> Money:
        return Money(abs(self.target.amount - self.entry.amount))

    @property
    def expected_edge(self) -> Decimal:
        """Reward:risk ratio — the one edge estimate every strategy can supply without a
        strategy-specific forecast model. `confidence` (from historical stats or Jev) scales it
        into `score` for ranking; this property alone is not decision-ready. `risk_per_share` is
        always positive here: construction enforces strict stop/entry/target ordering."""

        return self.reward_per_share.amount / self.risk_per_share.amount

    @property
    def score(self) -> Decimal:
        """The ranking key: expected edge weighted by how much to trust it."""

        return self.expected_edge * self.confidence
