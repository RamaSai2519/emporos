"""Hand-built option chains for the options tests (EM-226): small round numbers, so every expected
P&L can be worked out by hand. Nothing here is real market data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal

from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote, OptionRight
from emporos.options.chain_source import InMemoryChainSource
from emporos.options.entry import EntryPlanner
from emporos.options.fo_costs import FoFeeSchedule
from emporos.options.spread import SpreadPlan

PUT, CALL = OptionRight.PUT, OptionRight.CALL
DAY0 = date(2026, 1, 5)
LOT = 10
STEP = Decimal(10)
TICK = Decimal("0.05")

ZERO_FEES = FoFeeSchedule(
    "zero", date(2020, 1, 1), False, Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0),
    Decimal(0), Decimal(0), Decimal(0),
)  # fmt: skip

Prices = Mapping[tuple[int, OptionRight], str]


def snapshot(
    day: date,
    spot: str,
    expiry: date,
    prices: Prices,
    *,
    traded: int = 50,
    untraded: frozenset[tuple[int, OptionRight]] = frozenset(),
    lot_size: int = LOT,
) -> ChainSnapshot:
    """One expiry; `prices` maps (strike, right) to the close and the settle. A contract listed in
    `untraded` shows zero contracts traded (its close is stale, as the exchange's file has it)."""
    quotes = {}
    for (strike, right), price in prices.items():
        cost = Decimal(price)
        quotes[(Decimal(strike), right)] = OptionQuote(
            Decimal(strike), right, cost, cost, 1000, 0 if (strike, right) in untraded else traded
        )
    return ChainSnapshot(
        day, "TEST", Decimal(spot), lot_size, STEP, TICK, {expiry: ExpiryChain(expiry, quotes)}
    )


def source(*snapshots: ChainSnapshot) -> InMemoryChainSource:
    return InMemoryChainSource(snapshots)


class ScriptedPlanner(EntryPlanner):
    """Plans the same spread on the listed days and never otherwise."""

    def __init__(self, plan: SpreadPlan, days: frozenset[date]) -> None:
        self._plan, self._days = plan, days

    def plan(self, snapshot: ChainSnapshot) -> SpreadPlan | None:
        return self._plan if snapshot.day in self._days else None


def days_after(n: int) -> date:
    return DAY0 + timedelta(days=n)
