"""One attempted cross-sectional residual-momentum evaluation (EM-179), pure domain — the
cross-sectional counterpart of `emporos.domain.feature_trials.FeatureTrial`. Kept as its own
record, not a widening of `FeatureTrial`, because a tail's evidence (which tail, which signal
horizon produced the ranking, which holding horizon was tested) has no analogue in a single-
instrument feature trial: one class, one shape, per AGENTS.md's Single Responsibility rule.

A trial is immutable and only ever appended to a ledger, never edited or removed — the ledger
implementations live in `emporos.research.cross_sectional_ledger` (in-memory) and
`emporos.persistence.cross_sectional_ledger` (Mongo); both depend on this pure record, never the
reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.experiments import TrialRole


class DuplicateCrossSectionalTrialError(Exception):
    """A cross-sectional trial with this id is already in the ledger; never overwritten."""


@dataclass(frozen=True)
class CrossSectionalTrial:
    trial_id: str
    hypothesis_id: str
    signal_horizon_label: str  # the trailing window the ranking signal was computed over
    holding_horizon_label: str  # the forward window performance was tested over
    tail: str  # "top" or "bottom"
    role: TrialRole
    dataset_version: str
    cost_model: str
    regime_axis: str | None  # None = pooled across all regimes; else e.g. "volatility"
    regime_label: str | None
    sample_size: int
    gross_expectancy: Decimal | None
    net_expectancy: Decimal | None
    hit_rate: Decimal | None
    t_statistic: Decimal | None
    recorded_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("a cross-sectional trial needs an id")
        if self.tail not in ("top", "bottom"):
            raise ValueError("tail must be 'top' or 'bottom'")
        if self.recorded_at.tzinfo is None:
            raise ValueError("a cross-sectional trial's time must be timezone-aware")
        if self.sample_size < 0:
            raise ValueError("a sample size cannot be negative")
