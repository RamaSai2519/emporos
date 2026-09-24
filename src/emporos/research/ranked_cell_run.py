"""Screens a cell whose trades are chosen ACROSS the universe each day (EM-216, lane L17).

`CellScreenRun` screens every trade each instrument's scan takes. A ranked cell is different: each
arm's scan trades every qualifying signal and records it as a scored candidate, and only after
every instrument has been scanned does a `DailyTopKSelection` keep the day's top signals. The
selection is made from signals before fills, over candidates on non-quarantined days only, and
the kept trades go through the same `Screener`, ledger and S1/S2 bars as any other cell.

Instruments are still the OUTER loop (one instrument's bars in memory at a time); only the small
per-arm lists of trades and candidates are held across instruments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.orders import OrderSide
from emporos.domain.sizing import DeclaredSize
from emporos.research.cell_run import ArmResult, BarSource, ScreenUniverse
from emporos.research.daily_selection import DailyTopKSelection, ScoredSignal
from emporos.research.partition import DISCOVERY, DataSplit
from emporos.research.scans.base import ScanExecution, SignalScan
from emporos.research.scans.proven import is_parity_proven
from emporos.research.screen_evaluator import ScreenEvaluator
from emporos.research.screen_ledger import ScreenIdentity, ScreenLedger
from emporos.research.screen_trades import ScreenTrade
from emporos.research.screener import Screener

__all__ = ["RankedArmResult", "RankedCellScreenRun", "RankedScan", "RankedScanFactory"]


class RankedScan(SignalScan, Protocol):
    """A scan that also reports every scored signal it gave, filled or not."""

    @property
    def candidates(self) -> Sequence[ScoredSignal]: ...


RankedScanFactory = Callable[[Mapping[str, str], ScanExecution], RankedScan]


@dataclass(frozen=True)
class RankedArmResult:
    arm: ArmResult
    candidate_days: int  # sessions with at least one competing signal
    chosen: int  # signals the selection kept (a chosen signal may not have filled)
    long_trades: int
    short_trades: int


class RankedCellScreenRun:
    def __init__(
        self,
        scan_factory: RankedScanFactory,
        selection: DailyTopKSelection,
        evaluator: ScreenEvaluator,
        ledger: ScreenLedger,
        clock: Clock,
        universe: ScreenUniverse,
        split: DataSplit = DISCOVERY,
    ) -> None:
        self._scan_factory = scan_factory
        self._selection = selection
        self._evaluator = evaluator
        self._ledger = ledger
        self._clock = clock
        self._universe = universe
        self._split = split

    def run(
        self,
        hypothesis: str,
        arms: Sequence[Mapping[str, str]],
        bars: BarSource,
        size: DeclaredSize,
    ) -> list[RankedArmResult]:
        execution = ScanExecution(position_value=size.position_value)
        scans = [self._scan_factory(arm, execution) for arm in arms]
        trades: list[list[ScreenTrade]] = [[] for _ in arms]
        for instrument_id in self._universe.instrument_ids:
            series = bars.bars(instrument_id, self._split)
            for index, scan in enumerate(scans):
                trades[index].extend(scan.scan(instrument_id, series))
        return [
            self._screen(hypothesis, arm, scan, trades[i], size)
            for i, (arm, scan) in enumerate(zip(arms, scans, strict=True))
        ]

    def _admissible(self, instrument_id: str, day: date) -> bool:
        return self._split.contains(day) and not self._universe.is_quarantined(instrument_id, day)

    def _screen(
        self,
        hypothesis: str,
        arm: Mapping[str, str],
        scan: RankedScan,
        trades: Sequence[ScreenTrade],
        size: DeclaredSize,
    ) -> RankedArmResult:
        signals = [s for s in scan.candidates if self._admissible(s.instrument_id, s.day)]
        admitted = [t for t in trades if self._admissible(t.instrument_id, t.day)]
        dropped = len(trades) - len(admitted)
        chosen = self._selection.chosen(signals)
        kept = self._selection.keep(admitted, signals)
        identity = ScreenIdentity(
            hypothesis, dict(arm), self._universe.universe_label,
            self._split.first, self._split.last, size.position_value,
        )  # fmt: skip
        screener = Screener(
            self._evaluator, self._ledger, self._clock,
            verified_by_parity=is_parity_proven(scan.name),
        )  # fmt: skip
        longs = sum(1 for t in kept if t.side is OrderSide.BUY)
        return RankedArmResult(
            ArmResult(arm, screener.screen(identity, kept), dropped),
            len({s.day for s in signals}),
            len(chosen),
            longs,
            len(kept) - longs,
        )
