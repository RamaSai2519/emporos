"""One attempted feature/hypothesis evaluation (EM-178), pure domain — the feature-research
counterpart of `emporos.domain.experiments.Trial`. Kept separate from `Trial` itself (rather than
widening it) because a feature trial carries evaluation statistics (rank IC, t-statistic, sample
size) `Trial` has no room for, and `Trial`'s P&L fields have no meaning for a feature study: one
class, one shape, per AGENTS.md's Single Responsibility rule.

A trial is immutable and only ever appended to a ledger, never edited or removed — the ledger
implementations live in `emporos.research.ledger` (in-memory) and
`emporos.persistence.feature_ledger` (Mongo); both depend on this pure record, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.experiments import TrialRole


class DuplicateFeatureTrialError(Exception):
    """A feature trial with this id is already in the ledger; the ledger never overwrites."""


@dataclass(frozen=True)
class FeatureTrial:
    trial_id: str
    hypothesis_id: str
    feature_name: str
    feature_version: str
    horizon_label: str
    role: TrialRole
    dataset_version: str  # e.g. ResearchProvenance.universe_hash for the run this trial used
    cost_model: str
    regime_axis: str | None  # None = pooled across all regimes; else e.g. "volatility"
    regime_label: str | None  # the specific bucket within that axis, e.g. "high"
    sample_size: int
    conditional_expectancy: Decimal | None
    cost_adjusted_expectancy: Decimal | None
    hit_rate: Decimal | None
    rank_ic: Decimal | None
    decile_spread: Decimal | None
    t_statistic: Decimal | None
    recorded_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("a feature trial needs an id")
        if self.recorded_at.tzinfo is None:
            raise ValueError("a feature trial's time must be timezone-aware")
        if self.sample_size < 0:
            raise ValueError("a sample size cannot be negative")
