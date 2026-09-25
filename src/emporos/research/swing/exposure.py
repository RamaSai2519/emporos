"""The real gaps a book held through, and what they did to it (EM-228, EM-229).

A REAL gap (see `research.gap_classes`) is never removed from the series: a position held into it
takes its return, and the stop can only fill at the next open, gap included. So every report lists
each real gap that a held position went through, with the rupees it made or lost, and nothing about
those days is hidden by a data rule.

A position is held THROUGH the gap on session D of its name if it was bought before D and sold on or
after D (a sale at D's open takes the gap). The gap's rupees are the position's shares (on the
analysis basis) times the move from the name's previous analysis close to D's analysis open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.research.gap_classes import GapVerdict
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.simulator import SwingRun

__all__ = ["RealGapExposure", "real_gap_exposure"]


@dataclass(frozen=True)
class RealGapExposure:
    instrument_id: str
    day: date
    gap: Decimal  # open over the previous close, minus 1
    pnl: Decimal  # rupees the gap made (or lost) for the book, before costs
    reason: str


def real_gap_exposure(
    run: SwingRun, dataset: SwingDataset, real: Sequence[GapVerdict]
) -> tuple[RealGapExposure, ...]:
    found: list[RealGapExposure] = []
    for verdict in real:
        finding = verdict.finding
        if finding.instrument_id not in dataset.instrument_ids:
            continue
        series = dataset.series(finding.instrument_id)
        at = series.index_of(finding.day)
        if at is None or at == 0:
            continue
        for trade in run.trades:
            if trade.instrument_id != finding.instrument_id:
                continue
            if not trade.entry_day < finding.day <= trade.exit_day:
                continue
            entry = series.index_of(trade.entry_day)
            if entry is None:
                continue
            shares = Decimal(trade.quantity) / series.multipliers[entry]
            move = series.analysis[at].open.amount - series.analysis[at - 1].close.amount
            found.append(
                RealGapExposure(
                    finding.instrument_id,
                    finding.day,
                    finding.raw_ratio - 1,
                    shares * move,
                    verdict.reason,
                )
            )
    return tuple(sorted(found, key=lambda e: (e.day, e.instrument_id)))
