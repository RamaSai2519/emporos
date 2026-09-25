"""Raw daily bars to the dataset a swing screen reads (EM-228, EM-229).

The order is fixed and none of it is optional: raw daily bars for the named instruments and the
window, the discontinuity audit against the adjustment ledger (what the ledger explains is adjusted
out; every other >= 15% open gap is quarantined), then one `SwingSeries` per name on the analysis
basis. The audit runs on the SAME bars the screen will use, so a gap inside the window cannot be
missed and a gap outside it cannot be counted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.research.adjustments import AdjustmentLedger, PriceAdjuster
from emporos.research.discontinuities import AuditReport, DiscontinuityAudit
from emporos.research.swing.data import SwingDataset, SwingSeriesFactory

__all__ = ["DailyBarSource", "SwingDatasetBuild", "SwingDatasetBuilder"]


class DailyBarSource(Protocol):
    def bars(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        """The instrument's daily bars with a session day in [first, last], oldest first."""
        ...


class _QuarantineSet:
    def __init__(self, pairs: frozenset[tuple[str, date]]) -> None:
        self._pairs = pairs

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) in self._pairs


@dataclass(frozen=True)
class SwingDatasetBuild:
    dataset: SwingDataset
    audit: AuditReport
    names_without_bars: tuple[str, ...]


class SwingDatasetBuilder:
    def __init__(self, source: DailyBarSource, ledger: AdjustmentLedger) -> None:
        self._source = source
        self._ledger = ledger

    def build(self, instrument_ids: Sequence[str], first: date, last: date) -> SwingDatasetBuild:
        raw = {i: list(self._source.bars(i, first, last)) for i in instrument_ids}
        present = {i: bars for i, bars in raw.items() if bars}
        audit = DiscontinuityAudit(self._ledger).audit_all(present.items())
        factory = SwingSeriesFactory(_QuarantineSet(frozenset(audit.quarantined)))
        adjuster = PriceAdjuster(self._ledger)
        series = [factory.build(adjuster.adjust(i, bars)) for i, bars in present.items()]
        missing = tuple(sorted(i for i in raw if i not in present))
        return SwingDatasetBuild(SwingDataset(series), audit, missing)
