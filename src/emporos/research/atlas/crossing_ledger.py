"""All the crossings of a fitted market: NIFTY, each sector, each stock (EM-243)."""

from __future__ import annotations

from datetime import date

from emporos.research.atlas.crossings import CrossingBlock, CrossingRules, CrossingScanner
from emporos.research.atlas.ledger import Fits, Fitted

__all__ = ["CrossingLedgerBuilder"]


def _who(fitted: Fitted) -> tuple[str, str, str, str]:
    w = fitted.who
    return (w.name, w.instrument_id, w.sector, w.group)


class CrossingLedgerBuilder:
    def __init__(self, sessions: tuple[date, ...], rules: CrossingRules | None = None) -> None:
        self._scanner = CrossingScanner(sessions, rules)

    def build(self, fits: Fits) -> CrossingBlock:
        block = CrossingBlock()
        block.extend(
            self._scanner.intraday(_who(fits.market), fits.market.own, fits.market.deco, "index")
        )
        for sector in fits.sectors:
            block.extend(self._scanner.intraday(_who(sector), sector.own, sector.deco, "sector"))
        for stock in fits.stocks:
            block.extend(self._scanner.intraday(_who(stock), stock.own, stock.deco, "stock"))
            block.extend(self._scanner.swing(_who(stock), stock.own, stock.deco))
        return block
