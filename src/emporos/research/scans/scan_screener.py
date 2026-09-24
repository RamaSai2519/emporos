"""Runs a scan over bars and screens what it trades (EM-191 F3b, EDGE_SEARCH_PLAN.md §4.1).

One job: bars in, a recorded `ScreenResult` out. Whether the result is advisory is not the caller's
choice: it is advisory unless the scan is one `PARITY_PROVEN` vouches for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.research.scans.base import SignalScan
from emporos.research.scans.proven import is_parity_proven
from emporos.research.screen_evaluator import ScreenEvaluator, ScreenResult
from emporos.research.screen_ledger import ScreenIdentity, ScreenLedger
from emporos.research.screener import Screener

__all__ = ["ScanScreener"]


class ScanScreener:
    def __init__(
        self, scan: SignalScan, evaluator: ScreenEvaluator, ledger: ScreenLedger, clock: Clock
    ) -> None:
        self._scan = scan
        self._screener = Screener(
            evaluator, ledger, clock, verified_by_parity=is_parity_proven(scan.name)
        )

    def screen(
        self, identity: ScreenIdentity, bars_by_instrument: Mapping[str, Sequence[Candle]]
    ) -> ScreenResult:
        trades = [
            trade
            for instrument_id in sorted(bars_by_instrument)
            for trade in self._scan.scan(instrument_id, bars_by_instrument[instrument_id])
        ]
        return self._screener.screen(identity, trades)
