"""Defined-risk spreads, correct by construction (EM-226, PROFIT_PLAN.md §3 and §5).

A `Vertical` is a short option and a long option of the same right, the long one farther out of the
money. A `SpreadPlan` is one or two verticals (a put credit spread, or an iron condor when it holds
one of each right). There is no type for a lone short option, so a naked short cannot be built, not
merely be forbidden (PROFIT_PLAN §0)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.domain.orders import OrderSide
from emporos.options.chain import OptionRight

__all__ = ["Leg", "LegFill", "SpreadPlan", "Vertical"]


@dataclass(frozen=True)
class Leg:
    strike: Decimal
    right: OptionRight
    side: OrderSide  # SELL is the short leg, BUY the long leg


@dataclass(frozen=True)
class LegFill:
    leg: Leg
    price: Decimal  # per unit, after slippage
    units: int  # lots x lot size

    @property
    def turnover(self) -> Decimal:
        return self.price * self.units


@dataclass(frozen=True)
class Vertical:
    right: OptionRight
    short_strike: Decimal
    long_strike: Decimal

    def __post_init__(self) -> None:
        further_out = (
            self.long_strike < self.short_strike
            if self.right is OptionRight.PUT
            else self.long_strike > self.short_strike
        )
        if not further_out:
            raise ValueError("the long leg must be farther out of the money than the short leg")

    @property
    def width(self) -> Decimal:
        return abs(self.short_strike - self.long_strike)

    @property
    def legs(self) -> tuple[Leg, Leg]:
        return (
            Leg(self.short_strike, self.right, OrderSide.SELL),
            Leg(self.long_strike, self.right, OrderSide.BUY),
        )

    def settlement_debit(self, underlying: Decimal) -> Decimal:
        """Per unit, what closing this vertical costs at expiry on a cash-settled index: the short
        leg's intrinsic value less the long leg's, which is between zero and the width."""
        depth = (
            self.short_strike - underlying
            if self.right is OptionRight.PUT
            else underlying - self.short_strike
        )
        return max(Decimal(0), min(self.width, depth))


@dataclass(frozen=True)
class SpreadPlan:
    expiry: date
    verticals: tuple[Vertical, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.verticals) <= 2:
            raise ValueError("a plan is one vertical (credit spread) or two (iron condor)")
        rights = [v.right for v in self.verticals]
        if len(set(rights)) != len(rights):
            raise ValueError("two verticals of the same right are not a defined structure here")
        if len(self.verticals) == 2:
            put = next(v for v in self.verticals if v.right is OptionRight.PUT)
            call = next(v for v in self.verticals if v.right is OptionRight.CALL)
            if put.short_strike >= call.short_strike:
                raise ValueError("an iron condor's put short must lie below its call short")

    @property
    def legs(self) -> Iterator[Leg]:
        for vertical in self.verticals:
            yield from vertical.legs

    @property
    def max_width(self) -> Decimal:
        """Only one wing can finish in the money, so the worst case is the wider wing."""
        return max(v.width for v in self.verticals)

    def settlement_debit(self, underlying: Decimal) -> Decimal:
        return sum((v.settlement_debit(underlying) for v in self.verticals), Decimal(0))
