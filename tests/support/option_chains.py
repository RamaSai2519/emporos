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
    settled = {expiry: Decimal(spot)} if day == expiry else {}
    return ChainSnapshot(
        day,
        "TEST",
        Decimal(spot),
        lot_size,
        STEP,
        TICK,
        {expiry: ExpiryChain(expiry, quotes)},
        settled,
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


# --- a synthetic market for end-to-end runs -----------------------------------------------------
# NIFTY drifts up with a slow wave, India VIX swings between about 13 and 17, options are priced by
# Black-Scholes at that VIX. The floats stay in this test helper: the code under test sees Decimals.

import math  # noqa: E402

from emporos.options.chain_source import ChainSource  # noqa: E402


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _put_price(spot: float, strike: float, years: float, vol: float) -> Decimal:
    if years <= 0:
        value = max(strike - spot, 0.0)
    else:
        d1 = (math.log(spot / strike) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
        d2 = d1 - vol * math.sqrt(years)
        value = strike * _cdf(-d2) - spot * _cdf(-d1)
    ticks = max(round(value / 0.05), 1)
    return Decimal(ticks) * Decimal("0.05")


def sessions(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def last_thursday(year: int, month: int) -> date:
    day = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    while day.weekday() != 3:
        day -= timedelta(days=1)
    return day


class SyntheticMarket:
    def __init__(self, first: date = date(2022, 1, 3), last: date = date(2025, 6, 30)) -> None:
        self.days = sessions(first, last)
        self.nifty = {
            d: Decimal(20000 + 6 * i) + Decimal(round(400 * math.sin(i / 25)))
            for i, d in enumerate(self.days)
        }
        self.vix = {
            d: Decimal("15") + Decimal(round(20 * math.sin(i / 11))) / 10
            for i, d in enumerate(self.days)
        }
        months = sorted({(d.year, d.month) for d in self.days})
        self.monthly = [last_thursday(y, m) for y, m in months]
        self.monthly += [last_thursday(y + (m == 12), m % 12 + 1) for y, m in months[-1:]]
        self.monthly = sorted(set(self.monthly))

    def snapshots(self) -> list[ChainSnapshot]:
        out = []
        for day in self.days:
            spot = float(self.nifty[day])
            vol = float(self.vix[day]) / 100
            listed = [e for e in self.monthly if e >= day][:3]
            base = round(spot / 50) * 50
            expiries = {}
            for expiry in listed:
                years = (expiry - day).days / 365
                quotes = {}
                for k in range(-50, 51):
                    strike = base + 50 * k
                    price = _put_price(spot, strike, years, vol)
                    quotes[(Decimal(strike), PUT)] = OptionQuote(
                        Decimal(strike), PUT, price, price, 5000, 100
                    )
                expiries[expiry] = ExpiryChain(expiry, quotes)
            settled = {day: self.nifty[day]} if day in listed else {}
            out.append(
                ChainSnapshot(
                    day, "NIFTY", self.nifty[day], 25, Decimal(50), TICK, expiries, settled
                )
            )
        return out

    def source(self) -> ChainSource:
        return InMemoryChainSource(self.snapshots())

    def futures_calendar(self) -> dict[date, frozenset[date]]:
        return {d: frozenset(e for e in self.monthly if e >= d) for d in self.days}
