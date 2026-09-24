"""Screens one scan's trades and records the look (EM-191 F3, EDGE_SEARCH_PLAN.md §4.1).

The one entry point for a screen: it sizes and prices the trades, applies the S2 bar, and appends
the screen to the ledger before returning, so no result exists that N does not know about.
"""

from __future__ import annotations

from collections.abc import Sequence

from emporos.core.clock import Clock
from emporos.research.screen_evaluator import ScreenEvaluator, ScreenResult
from emporos.research.screen_ledger import ScreenIdentity, ScreenLedger, ScreenRecord
from emporos.research.screen_trades import DeclaredValueSizer, ScreenTrade

__all__ = ["Screener"]


class Screener:
    def __init__(
        self,
        evaluator: ScreenEvaluator,
        ledger: ScreenLedger,
        clock: Clock,
        *,
        verified_by_parity: bool = False,
    ) -> None:
        self._evaluator = evaluator
        self._ledger = ledger
        self._clock = clock
        self._advisory = not verified_by_parity

    def screen(self, identity: ScreenIdentity, trades: Sequence[ScreenTrade]) -> ScreenResult:
        result = self._evaluator.evaluate(
            trades, DeclaredValueSizer(identity.position_value), advisory=self._advisory
        )
        self._ledger.record(ScreenRecord(identity, result, self._clock.now()))
        return result
