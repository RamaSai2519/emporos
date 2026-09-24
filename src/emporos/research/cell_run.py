"""Screens every declared arm of one search-map cell over the Discovery split (EM-191 §7.3).

One job: for each instrument, scan every arm over its bars, drop the days the history audit
quarantined, then hand each arm's trades to the `Screener`, which prices them at the declared size,
applies S1 and S2, and appends the look to the ledger. Instruments are the OUTER loop, so only one
instrument's bars are in memory at a time (a decade of 5m bars is ~180k candles per name).

The bars come through a `BarSource`, and the wiring hands it a vaulted reader: this class cannot
ask for a day outside the split it was given, and the split is checked here as well.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.domain.sizing import DeclaredSize
from emporos.research.partition import DISCOVERY, DataSplit
from emporos.research.scans.base import ScanExecution, SignalScan
from emporos.research.scans.proven import is_parity_proven
from emporos.research.screen_evaluator import ScreenEvaluator, ScreenResult
from emporos.research.screen_ledger import ScreenIdentity, ScreenLedger
from emporos.research.screen_trades import ScreenTrade
from emporos.research.screener import Screener

__all__ = ["ArmResult", "BarSource", "CellScreenRun", "ScanFactory", "ScreenUniverse"]

ScanFactory = Callable[[Mapping[str, str], ScanExecution], SignalScan]


class BarSource(Protocol):
    def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
        """One instrument's 5m bars over the split's days, oldest first."""
        ...


class ScreenUniverse(Protocol):
    """Which instruments a cell screens, and which of their days it must skip. The 29-name history
    audit is one (`HistoryAudit`); the D1 wider universe is another (`D1Universe`)."""

    @property
    def instrument_ids(self) -> Sequence[str]: ...

    @property
    def universe_label(self) -> str:
        """A short stable name for the universe, for a screen's identity."""
        ...

    def is_quarantined(self, instrument_id: str, day: date) -> bool: ...


@dataclass(frozen=True)
class ArmResult:
    parameters: Mapping[str, str]
    result: ScreenResult
    quarantined_dropped: int  # trades on quarantined instrument-days that were left out


class CellScreenRun:
    def __init__(
        self,
        scan_factory: ScanFactory,
        evaluator: ScreenEvaluator,
        ledger: ScreenLedger,
        clock: Clock,
        universe: ScreenUniverse,
        split: DataSplit = DISCOVERY,
    ) -> None:
        self._scan_factory = scan_factory
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
    ) -> list[ArmResult]:
        execution = ScanExecution(position_value=size.position_value)
        scans = [self._scan_factory(arm, execution) for arm in arms]
        trades: list[list[ScreenTrade]] = [[] for _ in arms]
        dropped = [0] * len(arms)
        for instrument_id in self._universe.instrument_ids:
            series = bars.bars(instrument_id, self._split)
            for index, scan in enumerate(scans):
                for trade in scan.scan(instrument_id, series):
                    if not self._split.contains(trade.day):
                        continue  # a scan never trades outside its bars; belt and braces
                    if self._universe.is_quarantined(instrument_id, trade.day):
                        dropped[index] += 1
                        continue
                    trades[index].append(trade)
        return [
            self._screen(hypothesis, arm, scan, trades[i], dropped[i], size)
            for i, (arm, scan) in enumerate(zip(arms, scans, strict=True))
        ]

    def _screen(
        self,
        hypothesis: str,
        arm: Mapping[str, str],
        scan: SignalScan,
        trades: Sequence[ScreenTrade],
        dropped: int,
        size: DeclaredSize,
    ) -> ArmResult:
        identity = ScreenIdentity(
            hypothesis, dict(arm), self._universe.universe_label,
            self._split.first, self._split.last, size.position_value,
        )  # fmt: skip
        screener = Screener(
            self._evaluator,
            self._ledger,
            self._clock,
            verified_by_parity=is_parity_proven(scan.name),
        )
        return ArmResult(arm, screener.screen(identity, trades), dropped)
