"""One attempted intraday lead-lag evaluation (EM-180), pure domain — a sibling of
`emporos.domain.feature_trials.FeatureTrial` and `emporos.domain.cross_sectional_trials.
CrossSectionalTrial`, not a repurposing of either: a lead-lag trial's identity is a predictor
(the market, or a named sector), what it predicted (a stock or a sector aggregate), two horizons
(the early window and the target window) and a direction (which early-return sign this trial's
statistics were conditioned on) — no existing record has room for that shape.

A trial is immutable and only ever appended to a ledger, never edited or removed — the ledger
implementations live in `emporos.research.lead_lag_ledger` (in-memory) and
`emporos.persistence.lead_lag_ledger` (Mongo); both depend on this pure record, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.experiments import TrialRole


class DuplicateLeadLagTrialError(Exception):
    """A lead-lag trial with this id is already in the ledger; the ledger never overwrites."""


@dataclass(frozen=True)
class LeadLagTrial:
    trial_id: str
    hypothesis_id: str
    predictor: str  # "market" or "sector:<name>"
    expression: str  # "stock" or "sector" — what the TARGET side is
    subject: str  # instrument id (expression == "stock") or sector name (expression == "sector")
    early_horizon_label: str
    target_horizon_label: str  # e.g. "30m", or "late_session"
    direction: str  # "up" or "down" — which early-return sign this trial is conditioned on
    role: TrialRole
    dataset_version: str
    cost_model: str
    regime_axis: str | None
    regime_label: str | None
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
            raise ValueError("a lead-lag trial needs an id")
        if self.expression not in ("stock", "sector"):
            raise ValueError("expression must be 'stock' or 'sector'")
        if self.direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        if self.recorded_at.tzinfo is None:
            raise ValueError("a lead-lag trial's time must be timezone-aware")
        if self.sample_size < 0:
            raise ValueError("a sample size cannot be negative")
