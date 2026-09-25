"""The >=15% discontinuity audit for daily bars (EM-221, A-F2).

A session whose OPEN is 15% or more from the previous session's close is either a corporate action
the series has not been adjusted for, or a real move (results, a takeover bid, a circuit-breaker
day). From the bars alone the two cannot be told apart, so the audit makes no guess:

* **EXPLAINED**: the ledger has a factor whose ex-date falls in `(previous session, this session]`
  AND, once adjusted, the gap is under the limit. The action is on record and it fits.
* **MISMATCHED**: a factor is on record but the adjusted gap is still at the limit or beyond
  (a wrong ratio, a second action missing). Quarantined.
* **SPLIT_SHAPED**: no factor, and the gap is within a few percent of a ratio a split or bonus
  produces (1/2, 1/3, 2/3 ...). Quarantined; the likeliest missing ledger entries.
* **UNEXPLAINED**: no factor and not split-shaped. Quarantined, and possibly a real move: the
  quarantine costs a genuine event, never a false trade.

Everything but EXPLAINED is quarantined: a swing screen takes no trade that starts, ends or is held
across a quarantined session. The shape table is an annotation to size the problem, fixed here in
code; it decides nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from itertools import pairwise
from typing import Any

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.adjustments import AdjustedSeries, AdjustmentLedger, PriceAdjuster

__all__ = [
    "DISCONTINUITY_LIMIT", "AuditReport", "DiscontinuityAudit", "DiscontinuityStatus", "Finding",
    "SPLIT_RATIOS", "split_shape",
]  # fmt: skip

DISCONTINUITY_LIMIT = Decimal("0.15")  # the history audit's and the D1 profiler's own limit
SHAPE_TOLERANCE = Decimal("0.06")  # relative: an ordinary move rides on top of the ratio
SPLIT_RATIOS = tuple(
    Fraction(n, d)
    for n, d in ((1, 2), (1, 3), (1, 4), (1, 5), (1, 10), (2, 3), (3, 4), (2, 5), (3, 5), (1, 20))
) + tuple(Fraction(d, n) for n, d in ((1, 2), (1, 5), (1, 10)))  # consolidations: x2, x5, x10


class DiscontinuityStatus(StrEnum):
    EXPLAINED = "explained"
    MISMATCHED = "mismatched"
    SPLIT_SHAPED = "split_shaped"
    UNEXPLAINED = "unexplained"

    @property
    def quarantined(self) -> bool:
        return self is not DiscontinuityStatus.EXPLAINED


@dataclass(frozen=True)
class Finding:
    instrument_id: str
    day: date
    raw_ratio: Decimal  # open / previous close on raw prices
    adjusted_ratio: Decimal  # the same on adjusted prices
    status: DiscontinuityStatus
    shape: Fraction | None  # the split/bonus ratio it resembles, when it does


def split_shape(ratio: Decimal) -> Fraction | None:
    """The split/bonus/consolidation ratio `ratio` is within tolerance of, else None."""
    for candidate in SPLIT_RATIOS:
        target = Decimal(candidate.numerator) / Decimal(candidate.denominator)
        if abs(ratio / target - 1) <= SHAPE_TOLERANCE:
            return candidate
    return None


@dataclass(frozen=True)
class AuditReport:
    findings: tuple[Finding, ...]
    sessions_checked: int

    @property
    def quarantined(self) -> tuple[tuple[str, date], ...]:
        return tuple((f.instrument_id, f.day) for f in self.findings if f.status.quarantined)

    def by_status(self) -> Mapping[DiscontinuityStatus, int]:
        return {s: sum(1 for f in self.findings if f.status is s) for s in DiscontinuityStatus}

    def to_document(self, ledger_hash: str) -> dict[str, Any]:
        """A committed, diffable record of what the audit found and which ledger it ran against."""
        return {
            "ledger_hash": ledger_hash,
            "sessions_checked": self.sessions_checked,
            "limit": str(DISCONTINUITY_LIMIT),
            "by_status": {status.value: n for status, n in self.by_status().items()},
            "findings": [
                {
                    "instrument_id": f.instrument_id,
                    "day": f.day.isoformat(),
                    "raw_ratio": f"{f.raw_ratio:.4f}",
                    "status": f.status.value,
                    **({"shape": str(f.shape)} if f.shape is not None else {}),
                }
                for f in sorted(self.findings, key=lambda f: (f.instrument_id, f.day))
            ],
        }


class DiscontinuityAudit:
    def __init__(self, ledger: AdjustmentLedger, limit: Decimal = DISCONTINUITY_LIMIT) -> None:
        if not Decimal(0) < limit < Decimal(1):
            raise ValueError("the limit is a fraction between 0 and 1")
        self._ledger = ledger
        self._limit = limit
        self._adjuster = PriceAdjuster(ledger)

    def audit(self, instrument_id: str, raw: Sequence[Candle]) -> AuditReport:
        series = self._adjuster.adjust(instrument_id, raw)
        return AuditReport(tuple(self._findings(instrument_id, series)), len(raw))

    def audit_all(self, bars: Iterable[tuple[str, Sequence[Candle]]]) -> AuditReport:
        findings: list[Finding] = []
        checked = 0
        for instrument_id, raw in bars:
            report = self.audit(instrument_id, raw)
            findings.extend(report.findings)
            checked += report.sessions_checked
        return AuditReport(tuple(findings), checked)

    def _findings(self, instrument_id: str, series: AdjustedSeries) -> Iterable[Finding]:
        factors = self._ledger.factors_for(instrument_id)
        for (raw_prev, raw_now), (adj_prev, adj_now) in zip(
            pairwise(series.raw), pairwise(series.adjusted), strict=True
        ):
            raw_ratio = self._gap(raw_prev, raw_now)
            if raw_ratio is None or abs(raw_ratio - 1) < self._limit:
                continue
            adjusted_ratio = self._gap(adj_prev, adj_now)
            assert adjusted_ratio is not None  # the raw prices were positive: so are these
            before = raw_prev.ts.astimezone(IST).date()
            day = raw_now.ts.astimezone(IST).date()
            on_record = any(before < f.ex_date <= day for f in factors)
            if on_record and abs(adjusted_ratio - 1) < self._limit:
                status = DiscontinuityStatus.EXPLAINED
            elif on_record:
                status = DiscontinuityStatus.MISMATCHED
            elif split_shape(raw_ratio) is not None:
                status = DiscontinuityStatus.SPLIT_SHAPED
            else:
                status = DiscontinuityStatus.UNEXPLAINED
            yield Finding(
                instrument_id, day, raw_ratio, adjusted_ratio, status, split_shape(raw_ratio)
            )

    @staticmethod
    def _gap(previous: Candle, current: Candle) -> Decimal | None:
        if previous.close.amount <= 0:
            return None
        return current.open.amount / previous.close.amount
