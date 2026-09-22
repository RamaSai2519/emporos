"""Strategy registry metadata (EM-155 / EM-152 "Strategy Registry").

A `Strategy` subclass declares what it *is*; `StrategyMetadata` declares what the registry
knows *about* it — the facts the opportunity-selection engine needs to decide whether a
strategy is even eligible to run against a given instrument, timeframe and regime, without
opening the class itself. Historical performance is not embedded here (it would go stale the
moment a new curation run finishes); `historical_stats_ref` only points at where that lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from emporos.domain.candles import Timeframe
from emporos.strategies.regime import MarketRegime


class ValidationStatus(StrEnum):
    """How far a strategy has progressed through validation (plan.md / EM-166 graduation)."""

    RESEARCH = "research"
    BACKTESTED = "backtested"
    WALK_FORWARD_VALIDATED = "walk_forward_validated"
    OUT_OF_SAMPLE_VALIDATED = "out_of_sample_validated"


class DeploymentStatus(StrEnum):
    """Where a strategy currently runs. Distinct from `ValidationStatus`: a strategy can be
    fully OOS-validated and still sit at `CANDIDATE` deployment pending an operator decision."""

    CANDIDATE = "candidate"
    PAPER = "paper"
    LIVE_CONSERVATIVE = "live_conservative"
    PRODUCTION = "production"
    RETIRED = "retired"


@dataclass(frozen=True)
class StrategyMetadata:
    """Registry-level facts about a strategy family, independent of any one run's config.

    An empty `supported_timeframes` or `supported_regimes` means "unconstrained" — the strategy
    has not declared a restriction, so every timeframe/regime is eligible; this is the state a
    strategy registered without explicit metadata is left in. `supported_instrument_ids=None`
    means "no fixed universe" — eligibility for a given instrument is then decided entirely by
    regime/timeframe/feature compatibility, not by a hardcoded list. `historical_stats_ref` is an
    opaque pointer (e.g. a curation run id) into wherever backtest/walk-forward results are
    persisted; the registry does not own that data.
    """

    version: str
    supported_timeframes: frozenset[Timeframe]
    supported_regimes: frozenset[MarketRegime]
    required_features: frozenset[str] = field(default_factory=frozenset)
    supported_instrument_ids: frozenset[str] | None = None
    validation_status: ValidationStatus = ValidationStatus.RESEARCH
    deployment_status: DeploymentStatus = DeploymentStatus.CANDIDATE
    historical_stats_ref: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("a strategy needs a version")

    def supports(
        self,
        *,
        timeframe: Timeframe,
        regime: MarketRegime,
        instrument_id: str,
    ) -> bool:
        if self.supported_timeframes and timeframe not in self.supported_timeframes:
            return False
        if self.supported_regimes and regime not in self.supported_regimes:
            return False
        if self.supported_instrument_ids is not None:
            return instrument_id in self.supported_instrument_ids
        return True
