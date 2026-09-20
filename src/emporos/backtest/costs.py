"""What a backtest's fills cost (plan.md §10 cost model): the dated YAML schedule in force on the
day each fill happened, applied by the same `IntradayCharges` the paper broker uses.

    Fill ─▶ its IST trading day ─▶ ScheduleSource.schedule_for(day) ─▶ IntradayCharges ─▶ charges

A rate change is a new file in `config/fees/`, so an old session keeps the rates it was traded
under. The one awkward case is a run over days BEFORE the oldest schedule (the shipped file is
dated the day it was read from Angel One's tariff page, not the day the rates began):

* `StrictSchedules` refuses, loudly: no schedule, no number. The default.
* `EarliestBeforeFirst` opts in to using the oldest schedule for those earlier days. The days that
  relied on the assumption are counted and reported, so the number is never presented as if it
  were the rates of the time. Today's rates are usually a reasonable, not an exact, stand-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.backtest.orders import Fill
from emporos.core.clock import IST
from emporos.domain.fees import ChargeBreakdown, FeeSchedule, IntradayCharges
from emporos.domain.instruments import Exchange
from emporos.portfolio.fee_schedules import FeeScheduleError, FeeScheduleLibrary


class ScheduleSource(Protocol):
    def schedule_for(self, day: date) -> FeeSchedule: ...

    @property
    def assumed_days(self) -> frozenset[date]:
        """Days priced with a schedule that had not yet begun (empty for a strict source)."""
        ...


class StrictSchedules:
    def __init__(self, library: FeeScheduleLibrary) -> None:
        self._library = library

    def schedule_for(self, day: date) -> FeeSchedule:
        return self._library.for_date(day)

    @property
    def assumed_days(self) -> frozenset[date]:
        return frozenset()


class EarliestBeforeFirst:
    """Uses the oldest schedule for days before it existed, and remembers which days."""

    def __init__(self, library: FeeScheduleLibrary) -> None:
        self._library = library
        self._assumed: set[date] = set()

    def schedule_for(self, day: date) -> FeeSchedule:
        try:
            return self._library.for_date(day)
        except FeeScheduleError:
            self._assumed.add(day)
            return self._library.earliest

    @property
    def assumed_days(self) -> frozenset[date]:
        return frozenset(self._assumed)


@dataclass(frozen=True)
class CostSummary:
    schedules: tuple[tuple[str, date, bool], ...]  # (name, effective_from, verified), sorted
    assumed_days: int  # trading days priced with a schedule that did not yet exist
    all_verified: bool


class BacktestCosts:
    def __init__(self, schedules: ScheduleSource) -> None:
        self._schedules = schedules
        self._used: dict[tuple[str, date], FeeSchedule] = {}
        self._calculators: dict[tuple[str, date], IntradayCharges] = {}

    def charges(self, fill: Fill) -> ChargeBreakdown:
        schedule = self._schedules.schedule_for(fill.ts.astimezone(IST).date())
        key = (schedule.name, schedule.effective_from)
        self._used[key] = schedule
        calculator = self._calculators.setdefault(key, IntradayCharges(schedule))
        exchange = Exchange(fill.instrument_id.split(":", 1)[0])
        return calculator.for_trade(exchange, fill.side, fill.quantity, fill.price)

    def summary(self) -> CostSummary:
        used = sorted(self._used.values(), key=lambda s: (s.effective_from, s.name))
        return CostSummary(
            schedules=tuple((s.name, s.effective_from, s.verified) for s in used),
            assumed_days=len(self._schedules.assumed_days),
            all_verified=bool(used) and all(s.verified for s in used),
        )
