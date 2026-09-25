"""The risk rules: one class each, evaluated as an ordered list over an immutable snapshot, like
`emporos.risk` (EM-240). A rule returns a `Refusal` or None; it never mutates anything."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.risk.limits import RiskLimits
from emporos.eventtrader.risk.models import (
    EntryProposal,
    OpenPosition,
    Product,
    Refusal,
    RiskSnapshot,
    Sized,
)
from emporos.eventtrader.stages.models import Side

__all__ = [
    "DailyLossHalt",
    "EntryWindow",
    "MaxPositions",
    "NoNakedOptionSelling",
    "OnePositionPerName",
    "OpenRiskCap",
    "PositionValueCap",
    "PostureHold",
    "RiskRule",
    "StopSide",
    "StopUpdateGuard",
    "StopWidth",
    "SwingNeedsDelivery",
    "TotalLossKill",
    "standard_rules",
]

_HUNDRED = Decimal(100)


class RiskRule(Protocol):
    name: str

    def check(
        self, proposal: EntryProposal, sized: Sized, snapshot: RiskSnapshot
    ) -> Refusal | None: ...


class TotalLossKill:
    """Rs 25,000 lost in total (realised plus open marked): the track is over. The engine also
    exposes `tripped` so the runner closes everything; only the operator restarts it."""

    name = "total_loss_kill"

    def __init__(self, limits: RiskLimits) -> None:
        self._limit = limits.total_loss

    def tripped(self, snapshot: RiskSnapshot) -> bool:
        return snapshot.realized_total + snapshot.open_pnl <= -self._limit

    def check(self, proposal: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if self.tripped(s):
            return Refusal(self.name, f"the experiment has lost Rs {self._limit}: it has stopped")
        return None


class DailyLossHalt:
    """Today's loss reaches Rs 5,000: no new entries today. Today's P&L is the realised P&L plus
    each open position marked to market, a position that has run past its stop counted at the stop
    (a stop caps its loss), so the halt never counts a loss the stop would have prevented."""

    name = "daily_loss_halt"

    def __init__(self, limits: RiskLimits) -> None:
        self._limit = limits.daily_loss

    def check(self, proposal: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        today = s.realized_today + sum(
            (max(p.marked_pnl, -p.risk) for p in s.open_positions), Decimal(0)
        )
        if today <= -self._limit:
            return Refusal(self.name, f"today is at Rs {today} (limit -{self._limit})")
        return None


class OpenRiskCap:
    name = "open_risk_cap"

    def __init__(self, limits: RiskLimits) -> None:
        self._cap = limits.open_risk

    def check(self, proposal: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        total = s.open_risk + sized.risk
        if total > self._cap:
            return Refusal(self.name, f"open risk would be Rs {total}, over the Rs {self._cap} cap")
        return None


class StopWidth:
    """A cash stop wider than 5% intraday or 12% swing is refused: the width is the idea's risk,
    and the size would have to shrink to nothing."""

    name = "stop_width"

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.OPTION or p.stop_price is None:
            return None
        width = abs(p.entry_price - p.stop_price) / p.entry_price * _HUNDRED
        cap = (
            self._limits.intraday_stop_pct
            if p.product is Product.INTRADAY
            else self._limits.swing_stop_pct
        )
        if width > cap:
            return Refusal(self.name, f"the stop is {width:.2f}% away, over the {cap}% limit")
        return None


class StopSide:
    """A long's stop is below the entry, a short's above; a cash entry has a stop."""

    name = "stop_side"

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.OPTION:
            return None
        if p.stop_price is None:
            return Refusal(self.name, "a cash entry needs a stop")
        wrong = (
            p.stop_price >= p.entry_price if p.side is Side.LONG else p.stop_price <= p.entry_price
        )
        return Refusal(self.name, "the stop is on the wrong side of the entry") if wrong else None


class EntryWindow:
    """No intraday entry from 14:45 IST, none before the open, and none at a weekend."""

    name = "entry_window"

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is not Product.INTRADAY:
            return None
        local = p.at.astimezone(IST)
        if local.time() >= self._limits.no_intraday_entry_after:
            return Refusal(
                self.name, f"no intraday entry from {self._limits.no_intraday_entry_after}"
            )
        if local.time() < self._limits.session_open or local.weekday() >= 5:
            return Refusal(self.name, "the market is not open")
        return None


class OnePositionPerName:
    """One position per name; a second entry in a name we hold is an add, and we never average
    down (nor up)."""

    name = "one_position_per_name"

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        return Refusal(self.name, f"{p.name} is already held") if s.holds(p.name) else None


class MaxPositions:
    name = "max_positions"

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.INTRADAY:
            held, cap = s.count(Product.INTRADAY), self._limits.max_intraday_positions
        else:
            held, cap = s.count(Product.SWING, Product.OPTION), self._limits.max_swing_positions
        return (
            Refusal(self.name, f"{held} open already, the limit is {cap}") if held >= cap else None
        )


class PositionValueCap:
    name = "position_value_cap"

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.OPTION:
            return None
        cap = (
            self._limits.intraday_position_value
            if p.product is Product.INTRADAY
            else self._limits.swing_position_value
        )
        if sized.position_value > cap:
            return Refusal(self.name, f"a Rs {sized.position_value} position is over Rs {cap}")
        return None


class NoNakedOptionSelling:
    """An option is only ever bought: the premium is the whole risk."""

    name = "no_naked_option_selling"

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.OPTION and p.side is not Side.LONG:
            return Refusal(self.name, "options are only bought, never sold")
        return None


class SwingNeedsDelivery:
    """A cash swing position is a delivery buy: a bearish swing view is a put, not a short."""

    name = "swing_long_only"

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        if p.product is Product.SWING and p.side is Side.SHORT:
            return Refusal(self.name, "a swing short in cash is not possible: use a put")
        return None


class PostureHold:
    name = "posture_hold"

    def check(self, p: EntryProposal, sized: Sized, s: RiskSnapshot) -> Refusal | None:
        return Refusal(self.name, "today's posture is hold") if s.posture_scale <= 0 else None


class StopUpdateGuard:
    """A stop is only ever tightened. `widening` is what a runner asks before it moves one."""

    name = "stops_never_widen"

    @staticmethod
    def widening(position: OpenPosition, new_stop: Decimal) -> bool:
        if position.stop_price is None:
            return False
        return (
            new_stop < position.stop_price
            if position.side is Side.LONG
            else new_stop > position.stop_price
        )


def standard_rules(limits: RiskLimits | None = None) -> list[RiskRule]:
    """The rules in evaluation order: the ones that end the track first, then the day, then the
    idea, then the book."""
    limits = limits or RiskLimits()
    return [
        TotalLossKill(limits), DailyLossHalt(limits), PostureHold(), NoNakedOptionSelling(),
        SwingNeedsDelivery(), StopSide(), StopWidth(limits), EntryWindow(limits),
        OnePositionPerName(), MaxPositions(limits), PositionValueCap(limits), OpenRiskCap(limits),
    ]  # fmt: skip


def floor_units(amount: Decimal, unit: Decimal) -> int:
    """Whole units of `unit` that fit in `amount`."""
    return int((amount / unit).to_integral_value(ROUND_FLOOR))
