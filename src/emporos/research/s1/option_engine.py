"""The E2 options book: buy the modelled ATM index option on an index crossing (EM-248, declaration
`s1-size-target-trail`).

Same loop, limits, entry fill and exit policies as the cash book; what differs is the instrument:

* the contract is picked from the crossing bar's spot (`ContractPicker`, amendment A) and priced by
  the `PremiumModel` from the previous session's settle; a signal with no contract or no prior
  settle is skipped and counted;
* size: premium paid = min(80,000, 5,000 / stop fraction of the premium), whole lots of the day's
  lot size; the stop enforces the operator's loss limit;
* target T = the benchmark round-trip cost of the position (a fraction of the premium, on the
  largest whole-lot position the Rs 80,000 allows) plus p; the stop and trail rules act on the
  premium (`atr` = ATR(14) of the modelled premium, the previous session's bars priced with the
  same contract for what today has not yet printed);
* costs: `OptionCosts` (fees by the schedule in force that day, slippage as a cost: benchmark one
  tick + 0.5% a side, adverse two ticks + 1.5% and fees x1.5), charged exactly per trade."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

import numpy as np

from emporos.core.clock import IST
from emporos.eventtrader.replay.program_costs import OptionCosts
from emporos.eventtrader.replay.records import Scenario, TradeLeg
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.options.fo_costs import FoFeeSchedule, FoFeeScheduleLibrary
from emporos.research.s1.arms import Arm
from emporos.research.s1.bars import BarStore, DayBars, mean_true_range
from emporos.research.s1.engine import BookLimits, SignalLoop, Trade, walk_exit
from emporos.research.s1.exit_policies import Position, TargetThenTrail
from emporos.research.s1.fills import Miss, try_entry
from emporos.research.s1.option_contracts import Contract, ContractPicker
from emporos.research.s1.option_model import PremiumModel, PriorSession
from emporos.research.s1.signals import Signal

__all__ = ["OptionBookEngine", "OptionRoundTrip", "ProgramOptionCosts"]

MIN_PREMIUM = 0.10  # below this the modelled option is worthless: two ticks


class OptionRoundTrip(Protocol):
    def cost(
        self,
        contract: Contract,
        units: int,
        entry: float,
        exit_: float,
        day: date,
        scenario: Scenario,
    ) -> float: ...  # fmt: skip


class ProgramOptionCosts:
    """The program's option costs by the F&O fee schedule in force on the trade's day."""

    def __init__(self, library: FoFeeScheduleLibrary) -> None:
        self._library = library
        self._by_schedule: dict[FoFeeSchedule, OptionCosts] = {}

    def cost(
        self, contract: Contract, units: int, entry: float, exit_: float, day: date,
        scenario: Scenario,
    ) -> float:  # fmt: skip
        try:
            schedule = self._library.for_date(day)
        except ValueError:
            schedule = self._library.earliest
        model = self._by_schedule.setdefault(schedule, OptionCosts(schedule))
        when = datetime(day.year, day.month, day.day, 12, 0, tzinfo=IST)
        instrument = Instrument.CALL if contract.right.value == "CE" else Instrument.PUT
        leg = TradeLeg(
            contract.underlying, instrument, Product.OPTION, Side.LONG, units, when,
            Decimal(str(round(entry, 2))), when, Decimal(str(round(exit_, 2))),
        )  # fmt: skip
        return float(model.cost(leg, scenario))


class OptionBookEngine(SignalLoop):
    def __init__(
        self,
        store: BarStore,  # the INDEX spot bars
        picker: ContractPicker,
        prior: PriorSession,
        costs: OptionRoundTrip,
        limits: BookLimits | None = None,
    ) -> None:
        super().__init__(limits)
        self._store, self._picker, self._prior, self._costs = store, picker, prior, costs

    def _trade(self, arm: Arm, signal: Signal, skipped: Counter[str]) -> Trade | None:
        spot = self._store.day(signal.instrument_id, signal.day)
        anchor = None if spot is None else spot.index_ending_at(signal.minute)
        if spot is None or anchor is None:
            skipped["no_bars"] += 1
            return None
        # An option is BOUGHT either way: a down move buys the put.
        contract = self._picker.pick(
            signal.name, signal.day, float(spot.close[anchor]), signal.direction
        )
        if isinstance(contract, str):
            skipped[contract] += 1
            return None
        inputs = self._prior.inputs(contract, signal.day)
        if inputs is None:
            skipped["no_prior_settle"] += 1
            return None
        model = PremiumModel(contract, inputs)
        bars = model.bars(spot, signal.day)
        reference = float(bars.close[anchor])
        if reference < MIN_PREMIUM:
            skipped["no_premium"] += 1
            return None
        return self._enter(arm, signal, contract, model, bars, anchor, reference, skipped)

    def _enter(
        self, arm: Arm, signal: Signal, contract: Contract, model: PremiumModel, bars: DayBars,
        anchor: int, reference: float, skipped: Counter[str],
    ) -> Trade | None:  # fmt: skip
        lot_cost = reference * contract.lot
        lots_cap = math.floor(self._limits.size / lot_cost)
        if lots_cap < 1:
            skipped["too_small"] += 1
            return None
        probe = self._round_trip_fraction(contract, lots_cap, reference, signal.day)
        atr = self._premium_atr(model, signal, bars, anchor)
        position = Position(1, reference, atr, probe)
        target = probe + arm.target_p
        stop = arm.stop_rule().fraction(position, target)
        if not 0 < stop < 1:
            skipped["no_stop"] += 1
            return None
        lots = math.floor(min(self._limits.size, self._limits.option_risk / stop) / lot_cost)
        if lots < 1:
            skipped["too_small"] += 1
            return None
        units = lots * contract.lot
        fill = try_entry(bars, signal.minute, True, units)
        if isinstance(fill, Miss):
            skipped[fill.value] += 1
            return None
        exit_ = walk_exit(
            TargetThenTrail(target, arm.stop_rule(), arm.trail_rule()), position, bars, fill.index
        )
        if exit_ is None:
            skipped["no_exit"] += 1
            return None
        day = signal.day
        return Trade(
            signal.instrument_id, signal.name, day, signal.direction, units,
            int(bars.closes_at[fill.index]), reference, exit_.price, exit_.closes_at, exit_.reason,
            exit_.trailed, units * reference, (exit_.price - reference) * units,
            self._costs.cost(contract, units, reference, exit_.price, day, Scenario.BENCHMARK),
            self._costs.cost(contract, units, reference, exit_.price, day, Scenario.ADVERSE),
            contract.kind.value,
        )  # fmt: skip

    def _round_trip_fraction(self, contract: Contract, lots: int, price: float, day: date) -> float:
        units = lots * contract.lot
        cost = self._costs.cost(contract, units, price, price, day, Scenario.BENCHMARK)
        return cost / (units * price)

    def _premium_atr(
        self, model: PremiumModel, signal: Signal, today: DayBars, anchor: int
    ) -> float:
        highs, lows, closes = (list(x[: anchor + 1]) for x in (today.high, today.low, today.close))
        if len(closes) < 15:
            before = self._store.previous(signal.instrument_id, signal.day)
            if before is not None:
                earlier = _previous_day(signal.day)
                bars = model.bars(before, earlier)
                highs, lows, closes = (
                    list(b) + list(t)
                    for b, t in ((bars.high, highs), (bars.low, lows), (bars.close, closes))
                )
        return mean_true_range(np.array(highs), np.array(lows), np.array(closes))


def _previous_day(day: date) -> date:
    """The previous session's date, for pricing its bars only for the ATR: the model needs a date
    for the time to expiry, so the previous weekday is enough (a holiday moves it by a day or two,
    which changes an ATR by nothing that matters)."""
    step = date.fromordinal(day.toordinal() - 1)
    while step.weekday() >= 5:
        step = date.fromordinal(step.toordinal() - 1)
    return step
