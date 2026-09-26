"""Exit policies over 5-minute bars: a fixed target and stop, and the trails (EM-219, declaration
`s1-size-target-trail`).

An `ExitPolicy` is given a filled `Position` and returns a `Watch`; the watch is fed the bars AFTER
the entry bar, oldest first, and answers with an `Exit` or None. Nothing here knows where the entry
came from (a crossing, an atlas signal, a Track L decision) or what the position is (a stock, an
option premium): stage 2 reuses these classes on other entries unchanged.

Bar rules, fixed by the declaration and conservative:

* a bar that touches both the stop and the target (or the trailing stop) is taken as the STOP;
* a bar that OPENS beyond a stop fills at its open (a gap through the stop);
* the best price for a trail updates on a bar's high (low for a short), and the new stop applies
  from the NEXT bar;
* a stop only ever tightens;
* `run_exit` closes what is still open at the last bar that completes by the square-off minute.

Prices are float reference prices; costs and slippage are charged elsewhere, never baked in."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "AtrStop", "AtrTrail", "Bar", "Exit", "ExitPolicy", "ExitReason", "HalfPeakTrail",
    "HalfTargetStop", "LockFixedTrail", "OneToOneStop", "Position", "StopRule", "TargetStop",
    "TargetThenTrail", "TrailRule", "Watch", "run_exit",
]  # fmt: skip

SQUARE_OFF_MINUTE = 15 * 60 + 15  # 15:15 IST


class ExitReason(StrEnum):
    STOP = "stop"  # the initial stop
    TARGET = "target"  # a fixed target (TargetStop only)
    TRAIL = "trail"  # a trailing stop, after the target was touched
    SQUARE_OFF = "square_off"


@dataclass(frozen=True)
class Bar:
    closes_at: int  # minutes after midnight IST at which the bar ends
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Position:
    side: int  # +1 long, -1 short
    entry: float  # the reference entry price
    atr: float  # ATR(14) in price terms at entry (0 when unknown)
    cost: float  # round-trip cost at benchmark, a fraction of the entry notional

    def __post_init__(self) -> None:
        if self.side not in (1, -1) or self.entry <= 0 or self.atr < 0 or self.cost < 0:
            raise ValueError("a position has a side of +-1, a positive entry and no negative cost")


@dataclass(frozen=True)
class Exit:
    price: float
    closes_at: int
    reason: ExitReason
    trailed: bool  # the target had been touched (the trail was armed) before the exit


class Watch(Protocol):
    @property
    def armed(self) -> bool:
        """True once the target has been touched and a trail is running."""
        ...

    def on_bar(self, bar: Bar) -> Exit | None: ...


class ExitPolicy(Protocol):
    def watch(self, position: Position) -> Watch: ...


# --- stops and trails ---------------------------------------------------------------------------
class StopRule(Protocol):
    def fraction(self, position: Position, target: float) -> float:
        """The initial stop's distance from the entry, as a fraction of the entry price."""
        ...


class TrailRule(Protocol):
    def stop(self, position: Position, best: float, target: float) -> float:
        """The trailing stop PRICE once `best` (the highest price for a long, lowest for a short)
        has been reached and the target (a fraction) was touched."""
        ...


@dataclass(frozen=True)
class OneToOneStop:
    """The stop is as far as the target: risk one to reward one."""

    def fraction(self, position: Position, target: float) -> float:
        return target


@dataclass(frozen=True)
class HalfTargetStop:
    def fraction(self, position: Position, target: float) -> float:
        return target / 2


@dataclass(frozen=True)
class AtrStop:
    multiple: float = 1.0

    def fraction(self, position: Position, target: float) -> float:
        return self.multiple * position.atr / position.entry


@dataclass(frozen=True)
class LockFixedTrail:
    """Lock the target's gain, then trail the best price by a fixed gap."""

    gap: float  # a fraction of the best price

    def stop(self, position: Position, best: float, target: float) -> float:
        s = position.side
        lock = position.entry * (1 + s * target)
        trail = best * (1 - s * self.gap)
        return max(lock, trail) if s > 0 else min(lock, trail)


@dataclass(frozen=True)
class HalfPeakTrail:
    """Keep half of the best open gain, and never less than the round-trip cost."""

    def stop(self, position: Position, best: float, target: float) -> float:
        s = position.side
        gain = s * (best - position.entry)
        return position.entry + s * max(position.cost * position.entry, gain / 2)


@dataclass(frozen=True)
class AtrTrail:
    multiple: float = 1.0

    def stop(self, position: Position, best: float, target: float) -> float:
        s = position.side
        trail = best - s * self.multiple * position.atr
        floor = position.entry * (1 + s * position.cost)
        return max(trail, floor) if s > 0 else min(trail, floor)


# --- the policies -------------------------------------------------------------------------------
@dataclass(frozen=True)
class TargetStop:
    """A fixed stop and a fixed target, both exits, as fractions of the entry price."""

    stop: float
    target: float

    def watch(self, position: Position) -> Watch:
        return _FixedWatch(position, self.stop, self.target)


@dataclass(frozen=True)
class TargetThenTrail:
    """A stop until the target is touched; then no target exit at all, a trailing stop instead."""

    target: float  # a fraction of the entry price
    stop_rule: StopRule
    trail_rule: TrailRule

    def watch(self, position: Position) -> Watch:
        return _TrailWatch(position, self)


class _FixedWatch:
    def __init__(self, position: Position, stop: float, target: float) -> None:
        s = position.side
        self._s = s
        self._stop = position.entry * (1 - s * stop)
        self._target = position.entry * (1 + s * target)

    @property
    def armed(self) -> bool:
        return False

    def on_bar(self, bar: Bar) -> Exit | None:
        s = self._s
        if s * (bar.open - self._stop) <= 0:
            return Exit(bar.open, bar.closes_at, ExitReason.STOP, False)
        low_side = bar.low if s > 0 else bar.high
        high_side = bar.high if s > 0 else bar.low
        if s * (low_side - self._stop) <= 0:
            return Exit(self._stop, bar.closes_at, ExitReason.STOP, False)  # stop first
        if s * (high_side - self._target) >= 0:
            fill = bar.open if s * (bar.open - self._target) >= 0 else self._target
            return Exit(fill, bar.closes_at, ExitReason.TARGET, False)
        return None


class _TrailWatch:
    def __init__(self, position: Position, policy: TargetThenTrail) -> None:
        s = position.side
        self._p, self._policy, self._s = position, policy, s
        distance = policy.stop_rule.fraction(position, policy.target)
        if distance <= 0:
            raise ValueError("a stop needs a positive distance")
        self._stop = position.entry * (1 - s * distance)
        self._target = position.entry * (1 + s * policy.target)
        self._best: float | None = None  # set once the target is touched

    @property
    def armed(self) -> bool:
        return self._best is not None

    def on_bar(self, bar: Bar) -> Exit | None:
        s = self._s
        trailing = self._best is not None
        reason = ExitReason.TRAIL if trailing else ExitReason.STOP
        if s * (bar.open - self._stop) <= 0:
            return Exit(bar.open, bar.closes_at, reason, trailing)
        against = bar.low if s > 0 else bar.high
        toward = bar.high if s > 0 else bar.low
        if s * (against - self._stop) <= 0:
            return Exit(self._stop, bar.closes_at, reason, trailing)  # stop first
        if self._best is None:
            if s * (toward - self._target) < 0:
                return None
            self._best = toward  # the target is touched: the trail starts
        else:
            self._best = max(self._best, toward) if s > 0 else min(self._best, toward)
        tightened = self._policy.trail_rule.stop(self._p, self._best, self._policy.target)
        self._stop = max(self._stop, tightened) if s > 0 else min(self._stop, tightened)
        return None


def run_exit(
    policy: ExitPolicy,
    position: Position,
    bars: Sequence[Bar],
    square_off: int = SQUARE_OFF_MINUTE,
) -> Exit | None:
    """Walk the bars after the entry bar. Closes at the last bar that completes by `square_off`
    if nothing else did; None only when there is no such bar at all."""
    watch = policy.watch(position)
    last: Bar | None = None
    for bar in bars:
        if bar.closes_at > square_off:
            break
        exit_ = watch.on_bar(bar)
        if exit_ is not None:
            return exit_
        last = bar
    if last is None:
        return None
    return Exit(last.close, last.closes_at, ExitReason.SQUARE_OFF, watch.armed)
