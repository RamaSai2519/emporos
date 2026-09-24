"""The vocabulary of a research experiment report (EM-188): what was declared before a run, which
versions produced the numbers, the headline metrics every family shares, and the machine-readable
reasons behind the outcome. Pure data and its own invariants; how a report is built, stored and
rendered lives in the layers above."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from emporos.domain.experiments import Verdict


class ReasonCode(StrEnum):
    """A machine-readable reason behind a gate finding, one per gate.

    The human `detail` of a finding says what was measured; the code says which rule spoke, so
    reports across every strategy family can be grouped and compared by reason, not by prose.
    """

    NET_PNL_NOT_REAL = "net_pnl_not_real"
    ADVERSE_COSTS = "adverse_costs"
    TOO_FEW_WINDOWS_PROFITABLE = "too_few_windows_profitable"
    TOO_FEW_TRADES = "too_few_trades"
    TOO_LITTLE_HISTORY = "too_little_history"
    DRAWDOWN_OVER_BUDGET = "drawdown_over_budget"
    PROFIT_CONCENTRATED = "profit_concentrated"
    PARAMETER_UNSTABLE = "parameter_unstable"
    DSR_BELOW_THRESHOLD = "dsr_below_threshold"
    PBO_ABOVE_THRESHOLD = "pbo_above_threshold"
    EDGE_BELOW_COST_ERROR = "edge_below_cost_error"
    BELOW_BASELINE = "below_baseline"
    TOO_FEW_REGIMES = "too_few_regimes"
    HOLDOUT_NOT_RESERVED = "holdout_not_reserved"
    HOLDOUT_NOT_EVALUATED = "holdout_not_evaluated"
    NOT_PREDECLARED = "not_predeclared"


# ---------------------------------------------------------------------------------------------
# The experiment report: one standard shape for every research family (EM-188).
# ---------------------------------------------------------------------------------------------

SCHEMA_VERSION = 1

# What a report of evidence gathered before declarations existed says instead of inventing a
# rationale, and the note that marks such a report. Never upgraded: it cannot be ACCEPTED.
BACKFILLED_RATIONALE = "backfilled: not pre-declared"
BACKFILLED_NOT_PREDECLARED = "BACKFILLED_NOT_PREDECLARED"

_SLUG = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
_ID = re.compile(r"^EXP-\d{8}-[a-z0-9]+(?:[_-][a-z0-9]+)*-[0-9a-f]{8}$")
_DIGEST = re.compile(r"^[0-9a-f]{8}$")

# The JSON-safe subset a declaration flattens to, so a hash of it never depends on Python types.
JsonValue = str | int | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ExperimentFamily(StrEnum):
    STRATEGY = "strategy"
    FEATURE = "feature"
    CROSS_SECTIONAL = "cross_sectional"
    LEAD_LAG = "lead_lag"
    JEV_INCREMENTAL = "jev_incremental"


class ExperimentOutcomeLabel(StrEnum):
    """The verdict as reports word it. `Verdict.VALIDATED` is persisted (verdicts, trial ledger) and
    read by the launch gate, so it is not renamed: it maps to ACCEPTED at the report boundary."""

    ACCEPTED = "accepted"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


_OUTCOMES = {
    Verdict.VALIDATED: ExperimentOutcomeLabel.ACCEPTED,
    Verdict.INCONCLUSIVE: ExperimentOutcomeLabel.INCONCLUSIVE,
    Verdict.REJECTED: ExperimentOutcomeLabel.REJECTED,
}


def outcome_of(verdict: Verdict) -> ExperimentOutcomeLabel:
    return _OUTCOMES[verdict]


class FindingOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReasonFinding:
    """One reason behind an outcome: the rule that spoke (`code`, absent only on evidence recorded
    before codes existed), what it looked at, and what it concluded."""

    code: ReasonCode | None
    name: str
    outcome: FindingOutcome
    detail: str


@dataclass(frozen=True)
class DatePair:
    """An inclusive range of calendar days."""

    first: date
    last: date

    def __post_init__(self) -> None:
        if self.last < self.first:
            raise ValueError(
                f"a date range cannot end ({self.last}) before it starts ({self.first})"
            )


@dataclass(frozen=True)
class ExperimentPeriods:
    """The periods the evidence came from: `train` (where parameters were chosen), `validation`
    (the walk-forward out-of-sample span the verdict reads) and `holdout` (reserved, never seen
    while choosing anything). Any may be absent (None) when the experiment did not have it."""

    train: DatePair | None = None
    validation: DatePair | None = None
    holdout: DatePair | None = None

    def __post_init__(self) -> None:
        if self.holdout is None:
            return
        for name, period in (("train", self.train), ("validation", self.validation)):
            if period is not None and period.last >= self.holdout.first:
                raise ValueError(f"the {name} period reaches into the reserved holdout")


class ExperimentId:
    """`EXP-<YYYYMMDD declared>-<slug>-<8 hex of the declaration's content hash>`.

    Stable because it is a function of the declaration's content, never of when a run happened:
    re-running the same declaration is the same experiment; changing a word makes a new one. The
    hash itself is computed by whoever mints the id (this layer imports nothing from `core`)."""

    def __init__(self, value: str) -> None:
        if not _ID.match(value):
            raise ValueError(f"not an experiment id: {value!r}")
        self._value = value

    @classmethod
    def of(cls, declared: date, slug: str, digest: str) -> ExperimentId:
        if not _SLUG.match(slug):
            raise ValueError(f"not a slug: {slug!r}")
        if not _DIGEST.match(digest):
            raise ValueError("an experiment digest is 8 lowercase hex characters")
        return cls(f"EXP-{declared:%Y%m%d}-{slug}-{digest}")

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ExperimentId({self._value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ExperimentId) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class ExperimentDeclaration:
    """What was claimed BEFORE the run: the hypothesis, why it should be true, what would prove it
    false, and the parameters and features it is allowed to touch."""

    family: ExperimentFamily
    slug: str
    hypothesis: str
    economic_rationale: str
    falsification: str
    parameter_grid: Mapping[str, tuple[str, ...]]  # parameter -> the canonical values in the grid
    feature_versions: Mapping[str, str]  # feature -> its definition version
    declared_at: datetime

    def __post_init__(self) -> None:
        if not _SLUG.match(self.slug):
            raise ValueError(
                f"an experiment slug is lowercase words joined by - or _: {self.slug!r}"
            )
        for label, text in (
            ("hypothesis", self.hypothesis),
            ("economic rationale", self.economic_rationale),
            ("falsification", self.falsification),
        ):
            if not text.strip():
                raise ValueError(f"an experiment must declare its {label}")
        if self.declared_at.tzinfo is None:
            raise ValueError("a declaration's time must be timezone-aware")

    @property
    def is_predeclared(self) -> bool:
        """False for a backfilled declaration: the claim was reconstructed after the fact."""
        return self.economic_rationale != BACKFILLED_RATIONALE

    def canonical(self) -> dict[str, JsonValue]:
        """The declaration as JSON-safe content, the input to its id."""
        grid: dict[str, JsonValue] = {k: list(v) for k, v in sorted(self.parameter_grid.items())}
        features: dict[str, JsonValue] = dict(sorted(self.feature_versions.items()))
        return {
            "family": self.family.value,
            "slug": self.slug,
            "hypothesis": self.hypothesis,
            "economic_rationale": self.economic_rationale,
            "falsification": self.falsification,
            "parameter_grid": grid,
            "feature_versions": features,
            "declared_at": self.declared_at.isoformat(),
        }


@dataclass(frozen=True)
class DatasetVersion:
    universe_hash: str
    calendar_version: str
    quarantine_hash: str
    timeframe: str
    first: date
    last: date


@dataclass(frozen=True)
class CostModelVersion:
    fee_schedule_id: str | None
    slippage_bps: Decimal | None
    benchmark_hash: str | None  # of the benchmark file the thresholds and scenarios came from


@dataclass(frozen=True)
class VersionStamp:
    """Which config, data, cost model and code produced the numbers. None means not recorded."""

    behaviour_hash: str | None = None  # of the config judged, when one config was
    candidate_behaviour_hashes: Mapping[str, str] = field(
        default_factory=dict
    )  # per chosen candidate
    dataset: DatasetVersion | None = None
    cost_model: CostModelVersion | None = None
    code_revision: str | None = None


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: Decimal
    statutory: Decimal  # STT, exchange, SEBI, stamp duty and GST together
    spread: Decimal
    slippage: Decimal
    total: Decimal
    per_trade_bps: Decimal | None
    per_trade_inr: Decimal | None

    def __post_init__(self) -> None:
        if self.brokerage + self.statutory + self.spread + self.slippage != self.total:
            raise ValueError("a cost breakdown's parts must add up to its total")


@dataclass(frozen=True)
class RegimeMetrics:
    count: int
    net_pnl: Decimal
    win_rate: Decimal | None


@dataclass(frozen=True)
class WindowMetrics:
    index: int
    first: date
    last: date
    net_pnl: Decimal


@dataclass(frozen=True)
class ExperimentMetrics:
    """Headline numbers, the same for every family. None means not applicable (a feature study has
    no P&L) or not recorded; never zero standing in for either."""

    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    expectancy: Decimal | None = None
    profit_factor: Decimal | None = None
    max_drawdown: Decimal | None = None
    sharpe: Decimal | None = None  # annualised
    deflated_sharpe: Decimal | None = None
    pbo: Decimal | None = None
    trade_count: int | None = None
    win_rate: Decimal | None = None
    by_regime: Mapping[str, RegimeMetrics] = field(default_factory=dict)
    by_window: tuple[WindowMetrics, ...] = ()
    costs: CostBreakdown | None = None


@dataclass(frozen=True)
class ExperimentReport:
    experiment_id: ExperimentId
    declaration: ExperimentDeclaration
    versions: VersionStamp
    periods: ExperimentPeriods
    metrics: ExperimentMetrics
    outcome: ExperimentOutcomeLabel
    reasons: tuple[ReasonFinding, ...]
    notes: tuple[str, ...] = ()
    # Family-specific evidence carried verbatim (a curation's robustness document, say): kept for
    # whoever wants the detail, never read by anything that compares experiments across families.
    supporting: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.outcome is ExperimentOutcomeLabel.ACCEPTED:
            if self.periods.holdout is None:
                raise ValueError("an experiment with no reserved holdout cannot be accepted")
            if any(r.outcome is not FindingOutcome.PASS for r in self.reasons):
                raise ValueError("an accepted experiment cannot carry a failing or unknown reason")
            if not self.declaration.is_predeclared:
                raise ValueError("an experiment that was not pre-declared cannot be accepted")
        if not self.declaration.is_predeclared and BACKFILLED_NOT_PREDECLARED not in self.notes:
            raise ValueError(
                f"a backfilled report must carry the {BACKFILLED_NOT_PREDECLARED} note"
            )

    @property
    def primary_reasons(self) -> tuple[ReasonFinding, ...]:
        """What decided the outcome: the failures if any, else what was left unknown."""
        failing = tuple(r for r in self.reasons if r.outcome is FindingOutcome.FAIL)
        if failing:
            return failing
        return tuple(r for r in self.reasons if r.outcome is FindingOutcome.UNKNOWN)
