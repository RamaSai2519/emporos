"""Research bookkeeping values: one attempted experiment (a `Trial`) and how a strategy was judged.

Every backtest that is run in search of a result is a trial, whether it worked or not: the number
of trials is what tells you how much of a good-looking result is the luck of having looked often
(the multiple-testing correction in `emporos.backtest.robustness`). A trial is immutable and is
only ever appended to a ledger, never edited or removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Verdict(StrEnum):
    VALIDATED = "validated"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


class TrialRole(StrEnum):
    TRAIN = "train"  # a candidate scored on a training window, in the search for parameters
    TEST = "test"  # the chosen parameters scored once on a window they had not seen
    STANDALONE = "standalone"  # a run not part of a tuning search


class DuplicateTrialError(Exception):
    """A trial with this id is already in the ledger; the ledger never overwrites."""


@dataclass(frozen=True)
class Trial:
    trial_id: str
    experiment: str  # the research programme it belongs to, e.g. "curation-2026-09"
    strategy: str
    candidate: str
    role: TrialRole
    dataset_version: str
    config_hash: str | None
    cost_model: str
    recorded_at: datetime
    run_id: str | None = None
    trade_count: int | None = None
    net_pnl: Decimal | None = None
    daily_sharpe: Decimal | None = None  # per day, NOT annualised
    verdict: Verdict | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("a trial needs an id")
        if self.recorded_at.tzinfo is None:
            raise ValueError("a trial's time must be timezone-aware")
        if self.trade_count is not None and self.trade_count < 0:
            raise ValueError("a trade count cannot be negative")
