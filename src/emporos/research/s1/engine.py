"""One arm over a window: signals in time order, one position at a time, the risk engine's limits
(EM-219 S1, declaration `s1-size-target-trail`).

Per signal: skipped if a position is open (`busy`), if the day's loss limit was reached, or after a
kill; else sized (notional = min(80,000 x leverage, 2,000 / stop fraction), whole shares), tried on
the next bar (`fills.try_entry`), and walked forward by the arm's exit policy until it exits or the
15:15 square-off. The daily loss (Rs 5,000: no new entry that day) and total loss (Rs 25,000: the
arm is KILLED on that date) are measured on ADVERSE-cost P&L, the harsher scenario."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from emporos.eventtrader.replay.records import Scenario
from emporos.research.s1.arms import Arm
from emporos.research.s1.bars import BarStore, DayBars, atr_before
from emporos.research.s1.costs import CostCurve
from emporos.research.s1.exit_policies import Bar, Exit, ExitReason, Position, TargetThenTrail
from emporos.research.s1.fills import Miss, try_entry
from emporos.research.s1.signals import Signal

__all__ = ["BookEngine", "BookLimits", "RunResult", "Trade"]

SQUARE_OFF = 15 * 60 + 15
CAPITAL_SIZE = 80_000.0


@dataclass(frozen=True)
class BookLimits:
    cash_risk: float = 2_000.0  # the stop caps the loss here
    daily_loss: float = 5_000.0
    total_loss: float = 25_000.0
    size: float = CAPITAL_SIZE  # 80% of the Rs 1,00,000 capital, before leverage


@dataclass(frozen=True)
class Trade:
    instrument_id: str
    name: str
    day: date
    direction: int
    quantity: int
    entry_minute: int
    entry: float
    exit_price: float
    exit_minute: int
    reason: ExitReason
    trailed: bool
    notional: float
    gross: float
    cost_benchmark: float
    cost_adverse: float

    def net(self, scenario: Scenario) -> float:
        return self.gross - (
            self.cost_benchmark if scenario is Scenario.BENCHMARK else self.cost_adverse
        )


@dataclass
class RunResult:
    trades: list[Trade] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    signals: int = 0
    killed_on: date | None = None

    def net(self, scenario: Scenario) -> float:
        return sum(t.net(scenario) for t in self.trades)


class BookEngine:
    def __init__(
        self,
        store: BarStore,
        curves: Mapping[int, CostCurve],  # direction (+1 long, -1 short) -> its cost curve
        limits: BookLimits | None = None,
    ) -> None:
        self._store, self._curves = store, dict(curves)
        self._limits = limits or BookLimits()

    def run(
        self, arm: Arm, days: Sequence[date], signals: Mapping[date, Sequence[Signal]]
    ) -> RunResult:
        result = RunResult()
        total = 0.0
        for day in days:
            today = signals.get(day, ())
            day_pnl, busy_until, halted = 0.0, 0, False
            for signal in today:
                result.signals += 1
                if result.killed_on is not None:
                    result.skipped["killed"] += 1
                elif signal.minute < busy_until:
                    result.skipped["busy"] += 1
                elif halted:
                    result.skipped["daily_limit"] += 1
                else:
                    trade = self._trade(arm, signal, result.skipped)
                    if trade is None:
                        continue
                    result.trades.append(trade)
                    net = trade.net(Scenario.ADVERSE)
                    day_pnl += net
                    total += net
                    busy_until = trade.exit_minute
                    halted = day_pnl <= -self._limits.daily_loss
                    if total <= -self._limits.total_loss:
                        result.killed_on = day
        return result

    # --- one trade ---------------------------------------------------------------------------
    def _trade(self, arm: Arm, signal: Signal, skipped: Counter[str]) -> Trade | None:
        bars = self._store.day(signal.instrument_id, signal.day)
        anchor = None if bars is None else bars.index_ending_at(signal.minute)
        if bars is None or anchor is None:
            skipped["no_bars"] += 1
            return None
        reference = float(bars.close[anchor])
        curve = self._curves[signal.direction]
        size_cap = self._limits.size * arm.leverage
        probe = curve.fraction(size_cap, Scenario.BENCHMARK)  # the cost fraction at the size cap
        atr = atr_before(self._store, signal.instrument_id, signal.day, signal.minute)
        position = Position(signal.direction, reference, atr, probe)
        target = probe + arm.target_p
        stop = arm.stop_rule().fraction(position, target)
        if stop <= 0:
            skipped["no_stop"] += 1
            return None
        quantity = math.floor(min(size_cap, self._limits.cash_risk / stop) / reference)
        if quantity < 1:
            skipped["too_small"] += 1
            return None
        fill = try_entry(bars, signal.minute, signal.direction > 0, quantity)
        if isinstance(fill, Miss):
            skipped[fill.value] += 1
            return None
        exit_ = _walk(
            TargetThenTrail(target, arm.stop_rule(), arm.trail_rule()), position, bars, fill.index
        )
        if exit_ is None:
            skipped["no_exit"] += 1
            return None
        notional = quantity * reference
        gross = signal.direction * (exit_.price - reference) * quantity
        return Trade(
            signal.instrument_id, signal.name, signal.day, signal.direction, quantity,
            int(bars.closes_at[fill.index]), reference, exit_.price, exit_.closes_at, exit_.reason,
            exit_.trailed, notional, gross, curve.cost(notional, Scenario.BENCHMARK),
            curve.cost(notional, Scenario.ADVERSE),
        )  # fmt: skip


def _walk(
    policy: TargetThenTrail, position: Position, day: DayBars, fill_index: int
) -> Exit | None:
    """The bars AFTER the entry bar, to the 15:15 square-off."""
    watch = policy.watch(position)
    last: int | None = None
    for i in range(fill_index + 1, len(day)):
        minute = int(day.closes_at[i])
        if minute > SQUARE_OFF:
            break
        out = watch.on_bar(
            Bar(
                minute,
                float(day.open[i]),
                float(day.high[i]),
                float(day.low[i]),
                float(day.close[i]),
            )
        )
        if out is not None:
            return out
        last = i
    if last is None:
        return None
    return Exit(
        float(day.close[last]), int(day.closes_at[last]), ExitReason.SQUARE_OFF, watch.armed
    )
