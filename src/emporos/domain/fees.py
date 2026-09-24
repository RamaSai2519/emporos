"""Trading charges for a cash-equity trade, intraday or delivery (plan.md §10 cost model).

Pure arithmetic over an injected, dated `FeeSchedule`: no rate is hardcoded here, because these
rates change and are volatile. Every component is rounded to the paisa, half up, as charged.

A schedule belongs to one `TradeProduct`. Intraday pays STT on the sell only and no DP charge;
delivery pays STT on both sides, a higher stamp duty on the buy and a DP charge on each sale. The
product travels with the schedule so that a delivery file in `config/fees/` can never be picked up
by an intraday reader (`FeeScheduleLibrary.from_directory` filters on it).

Not modelled: NSE's investor-protection-fund levy (a fraction of the turnover fee, and part of the
GST base), exchange-specific BSE scrip groups (a single BSE rate is used), the rupee-rounding some
levies apply to a whole day's turnover, and, for delivery, that the DP charge is levied once per
scrip per day however many sell orders there are (`DeliveryCharges` charges it per sell order, the
right count for a swing book that sells a name once). A schedule built from these rates therefore
slightly UNDERSTATES real costs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import ClassVar

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_PAISA = Decimal("0.01")
_HUNDRED = Decimal(100)
_CRORE = Decimal(10_000_000)


class TradeProduct(StrEnum):
    INTRADAY = "intraday"
    DELIVERY = "delivery"


@dataclass(frozen=True)
class FeeSchedule:
    """The rates in force from `effective_from`. Percentages are in percent (0.025 = 0.025%)."""

    name: str
    effective_from: date
    brokerage_flat: Money  # cap per executed order
    brokerage_percent: Decimal  # brokerage is the lower of the flat cap and this share of turnover
    brokerage_minimum: Money
    stt_sell_percent: Decimal
    exchange_transaction_percent: dict[Exchange, Decimal]
    sebi_per_crore: Money
    stamp_duty_buy_percent: Decimal
    gst_percent: Decimal
    # Whether the rates were reconciled against a real contract note. False = read from a public
    # tariff page: fine for paper P&L, not for anything that must match a bill.
    verified: bool = False
    product: TradeProduct = TradeProduct.INTRADAY
    stt_buy_percent: Decimal = Decimal(0)  # delivery only: intraday pays no STT on the buy
    dp_charge_per_sale: Money = field(
        default_factory=Money.zero
    )  # delivery only: per sell order, before GST

    def __post_init__(self) -> None:
        percents = (
            self.brokerage_percent, self.stt_sell_percent, self.stt_buy_percent,
            self.stamp_duty_buy_percent, self.gst_percent,
            *self.exchange_transaction_percent.values(),
        )  # fmt: skip
        if any(p < 0 for p in percents):
            raise ValueError("a rate cannot be negative")
        charges = (
            self.brokerage_flat,
            self.brokerage_minimum,
            self.sebi_per_crore,
            self.dp_charge_per_sale,
        )
        if Money.zero() > min(charges):
            raise ValueError("a charge cannot be negative")
        if self.product is TradeProduct.INTRADAY and (
            self.stt_buy_percent or self.dp_charge_per_sale.amount
        ):
            raise ValueError("an intraday schedule has no STT on the buy and no DP charge")
        if not self.exchange_transaction_percent:
            raise ValueError("a schedule needs at least one exchange transaction rate")


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: Money
    stt: Money
    exchange_transaction: Money
    sebi: Money
    stamp_duty: Money
    gst: Money
    dp: Money = field(
        default_factory=Money.zero
    )  # the depository's charge on a delivery sale, GST on it is in `gst`

    @property
    def total(self) -> Money:
        return (
            self.brokerage + self.stt + self.exchange_transaction
            + self.sebi + self.stamp_duty + self.gst + self.dp
        )  # fmt: skip


class ScheduleCharges(ABC):
    """Applies a schedule to one executed order. What differs between products (STT, the DP
    charge) is a small hook a subclass fills; the brokerage, exchange, SEBI, stamp and GST
    arithmetic is shared, so the two products cannot drift apart on it."""

    product: ClassVar[TradeProduct]

    def __init__(self, schedule: FeeSchedule) -> None:
        if schedule.product is not self.product:
            raise ValueError(
                f"{type(self).__name__} needs a schedule for {self.product.value}, "
                f"got {schedule.name!r} ({schedule.product.value})"
            )
        self._schedule = schedule

    def for_trade(
        self, exchange: Exchange, side: OrderSide, quantity: int, price: Money
    ) -> ChargeBreakdown:
        if quantity <= 0:
            raise ValueError("a trade has a positive quantity")
        schedule = self._schedule
        try:
            transaction_rate = schedule.exchange_transaction_percent[exchange]
        except KeyError:
            raise ValueError(f"the schedule has no rate for {exchange.value}") from None
        turnover = price.amount * quantity
        selling = side is OrderSide.SELL

        brokerage = self._paisa(
            max(
                schedule.brokerage_minimum.amount,
                min(
                    schedule.brokerage_flat.amount, turnover * schedule.brokerage_percent / _HUNDRED
                ),
            )
        )
        transaction = self._paisa(turnover * transaction_rate / _HUNDRED)
        sebi = self._paisa(turnover * schedule.sebi_per_crore.amount / _CRORE)
        dp = self._paisa(self._dp_charge(selling))
        return ChargeBreakdown(
            brokerage=brokerage,
            stt=self._paisa(turnover * self._stt_percent(selling) / _HUNDRED),
            exchange_transaction=transaction,
            sebi=sebi,
            stamp_duty=self._paisa(
                0 if selling else turnover * schedule.stamp_duty_buy_percent / _HUNDRED
            ),
            gst=self._paisa(
                (brokerage.amount + transaction.amount + sebi.amount + dp.amount)
                * schedule.gst_percent
                / _HUNDRED
            ),
            dp=dp,
        )

    @abstractmethod
    def _stt_percent(self, selling: bool) -> Decimal: ...

    def _dp_charge(self, selling: bool) -> Decimal:
        return Decimal(0)

    @staticmethod
    def _paisa(amount: Decimal | int) -> Money:
        return Money(Decimal(amount).quantize(_PAISA, rounding=ROUND_HALF_UP))


class IntradayCharges(ScheduleCharges):
    """STT on the sell only; no DP charge (nothing is delivered)."""

    product: ClassVar[TradeProduct] = TradeProduct.INTRADAY

    def _stt_percent(self, selling: bool) -> Decimal:
        return self._schedule.stt_sell_percent if selling else Decimal(0)


class DeliveryCharges(ScheduleCharges):
    """STT on both sides, and the depository's charge (plus GST) on each sale."""

    product: ClassVar[TradeProduct] = TradeProduct.DELIVERY

    def _stt_percent(self, selling: bool) -> Decimal:
        schedule = self._schedule
        return schedule.stt_sell_percent if selling else schedule.stt_buy_percent

    def _dp_charge(self, selling: bool) -> Decimal:
        return self._schedule.dp_charge_per_sale.amount if selling else Decimal(0)
