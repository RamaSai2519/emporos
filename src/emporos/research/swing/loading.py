"""Raw daily bars to the dataset a swing screen reads (EM-228, EM-229).

The order is fixed and none of it is optional: raw daily bars for the named instruments and the
window; the discontinuity audit against the adjustment ledger (what the ledger explains is adjusted
out); the real-or-artifact rule (`gap_classes`) for every gap left; then one `SwingSeries` per name
on the analysis basis, in which ONLY the artifacts are flattened. A REAL gap stays in the series: a
strategy trades through it with its real P&L. The audit runs on the SAME bars the screen will use,
so a gap inside the window cannot be missed and a gap outside it cannot be counted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.research.adjustments import AdjustmentLedger, PriceAdjuster
from emporos.research.discontinuities import AuditReport, DiscontinuityAudit
from emporos.research.gap_classes import GapClass, GapClassifier, GapVerdict, IndexMoves
from emporos.research.swing.data import SwingDataset, SwingSeriesFactory

__all__ = ["DailyBarSource", "SwingDatasetBuild", "SwingDatasetBuilder"]


class DailyBarSource(Protocol):
    def bars(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        """The instrument's daily bars with a session day in [first, last], oldest first."""
        ...


class _ArtifactSet:
    def __init__(self, pairs: frozenset[tuple[str, date]]) -> None:
        self._pairs = pairs

    def is_artifact(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) in self._pairs


@dataclass(frozen=True)
class SwingDatasetBuild:
    dataset: SwingDataset
    audit: AuditReport
    verdicts: tuple[GapVerdict, ...]
    names_without_bars: tuple[str, ...]

    def of_class(self, gap_class: GapClass) -> tuple[GapVerdict, ...]:
        return tuple(v for v in self.verdicts if v.gap_class is gap_class)


class SwingDatasetBuilder:
    def __init__(self, source: DailyBarSource, ledger: AdjustmentLedger, index: IndexMoves) -> None:
        self._source = source
        self._ledger = ledger
        self._classifier = GapClassifier(index)

    def build(self, instrument_ids: Sequence[str], first: date, last: date) -> SwingDatasetBuild:
        raw = {i: list(self._source.bars(i, first, last)) for i in instrument_ids}
        present = {i: bars for i, bars in raw.items() if bars}
        audit = DiscontinuityAudit(self._ledger).audit_all(present.items())
        verdicts = tuple(self._classifier.classify(f) for f in audit.findings)
        artifacts = frozenset(
            (v.finding.instrument_id, v.finding.day)
            for v in verdicts
            if v.gap_class is GapClass.ARTIFACT
        )
        factory = SwingSeriesFactory(_ArtifactSet(artifacts))
        adjuster = PriceAdjuster(self._ledger)
        series = [factory.build(adjuster.adjust(i, bars)) for i, bars in present.items()]
        missing = tuple(sorted(i for i in raw if i not in present))
        return SwingDatasetBuild(SwingDataset(series), audit, verdicts, missing)
