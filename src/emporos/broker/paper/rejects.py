"""Configurable order rejection, each rule a small class evaluated in a stated order.

A rule says WHERE a rejection happens, because the real broker rejects in two ways and an
execution engine must handle both:

* AT_ACCEPTANCE — the API refuses the request outright (a bad price, an unsupported product).
  The caller gets a definitive `BrokerRejectedError` and no order exists.
* AT_EXCHANGE — the order is accepted, then refused downstream (risk/margin, an exchange reject).
  It appears in the order book as REJECTED, with the reason, and an update is pushed.

Adding a rule is adding a class; nothing here is edited to extend it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from emporos.broker.models import PlaceOrderRequest, ProductType
from emporos.broker.paper.chance import Chance
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType


class RejectionStage(StrEnum):
    AT_ACCEPTANCE = "AT_ACCEPTANCE"
    AT_EXCHANGE = "AT_EXCHANGE"


@dataclass(frozen=True)
class Rejection:
    reason: str
    stage: RejectionStage


@dataclass(frozen=True)
class OrderContext:
    """Everything a rule may look at when judging one order."""

    request: PlaceOrderRequest
    instrument: Instrument
    last_price: Money | None
    available_cash: Money
    margin_required: Money


class RejectRule(Protocol):
    @property
    def stage(self) -> RejectionStage: ...

    def reason(self, context: OrderContext) -> str | None:
        """Why the order must be refused, or None if this rule has no objection."""
        ...


class SupportedProductRule:
    """The platform trades cash INTRADAY only; the paper broker models no delivery settlement."""

    stage = RejectionStage.AT_ACCEPTANCE

    def reason(self, context: OrderContext) -> str | None:
        if context.request.product is not ProductType.INTRADAY:
            return f"product {context.request.product.value} is not simulated (INTRADAY only)"
        return None


class TickSizeRule:
    """Prices sit on the instrument's tick grid, as the exchange requires."""

    stage = RejectionStage.AT_ACCEPTANCE

    def reason(self, context: OrderContext) -> str | None:
        tick = context.instrument.tick_size.amount
        for label, price in (
            ("price", context.request.price),
            ("trigger", context.request.trigger_price),
        ):
            if price is not None and price.amount % tick != 0:
                return f"{label} {price.amount} is not a multiple of the tick size {tick}"
        return None


class StopPlacementRule:
    """A stop-loss limit's trigger must lie on the right side of its limit and of the market:
    a sell triggers below the market and limits at or below its trigger; a buy the mirror."""

    stage = RejectionStage.AT_ACCEPTANCE

    def reason(self, context: OrderContext) -> str | None:
        request = context.request
        trigger = request.trigger_price
        if request.order_type is not OrderType.STOPLOSS_LIMIT or trigger is None:
            return None
        buying = request.side is OrderSide.BUY
        if (request.price < trigger) if buying else (request.price > trigger):
            return "the limit price is on the wrong side of the trigger price"
        last = context.last_price
        if last is not None and ((trigger <= last) if buying else (trigger >= last)):
            return "the trigger price has already been reached"
        return None


class SufficientFundsRule:
    """Refuses an order whose margin exceeds the free cash — a risk-management reject."""

    stage = RejectionStage.AT_EXCHANGE

    def reason(self, context: OrderContext) -> str | None:
        if context.margin_required > context.available_cash:
            return (
                f"insufficient funds: need {context.margin_required.amount}, "
                f"available {context.available_cash.amount}"
            )
        return None


class ProbabilisticRejectRule:
    """Refuses a configurable share of orders at random, to exercise the caller's reject path."""

    stage = RejectionStage.AT_EXCHANGE

    def __init__(
        self, rate: Decimal, chance: Chance, reason: str = "simulated exchange reject"
    ) -> None:
        if not Decimal(0) <= rate <= Decimal(1):
            raise ValueError("a reject rate lies between 0 and 1")
        self._rate = rate
        self._chance = chance
        self._reason = reason

    def reason(self, context: OrderContext) -> str | None:
        return self._reason if self._chance.hits(self._rate) else None


class OrderScreen:
    """An ordered list of rules: the first objection wins."""

    def __init__(self, rules: Sequence[RejectRule]) -> None:
        self._rules = tuple(rules)

    def first_rejection(self, context: OrderContext) -> Rejection | None:
        for rule in self._rules:
            reason = rule.reason(context)
            if reason is not None:
                return Rejection(reason, rule.stage)
        return None

    def first_acceptance_rejection(self, context: OrderContext) -> Rejection | None:
        """Only the request-validation rules — what an amendment is checked against."""
        for rule in self._rules:
            if rule.stage is RejectionStage.AT_ACCEPTANCE:
                reason = rule.reason(context)
                if reason is not None:
                    return Rejection(reason, rule.stage)
        return None


def default_rules() -> tuple[RejectRule, ...]:
    """Request validation first, then the risk check; nothing random unless asked for."""
    return (SupportedProductRule(), TickSizeRule(), StopPlacementRule(), SufficientFundsRule())
