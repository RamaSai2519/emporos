"""Trading charges for a cash-equity INTRADAY trade (plan.md §10 cost model).

Pure arithmetic over an injected, dated `FeeSchedule`: no rate is hardcoded here, because these
rates change and are volatile. Every component is rounded to the paisa, half up, as charged.

Not modelled: NSE's investor-protection-fund levy (a fraction of the turnover fee, and part of the
GST base), DP charges (delivery only), exchange-specific BSE scrip groups (a single BSE rate is
used), and the rupee-rounding some levies apply to a whole day's turnover. A schedule built from
these rates therefore slightly UNDERSTATES real costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_PAISA = Decimal("0.01")
_HUNDRED = Decimal(100)
_CRORE = Decimal(10_000_000)


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

    def __post_init__(self) -> None:
        percents = (
            self.brokerage_percent, self.stt_sell_percent, self.stamp_duty_buy_percent,
            self.gst_percent, *self.exchange_transaction_percent.values(),
        )  # fmt: skip
        if any(p < 0 for p in percents):
            raise ValueError("a rate cannot be negative")
        if Money.zero() > min(self.brokerage_flat, self.brokerage_minimum, self.sebi_per_crore):
            raise ValueError("a charge cannot be negative")
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

    @property
    def total(self) -> Money:
        return (
            self.brokerage + self.stt + self.exchange_transaction
            + self.sebi + self.stamp_duty + self.gst
        )  # fmt: skip


class IntradayCharges:
    """Applies a schedule to one executed order."""

    def __init__(self, schedule: FeeSchedule) -> None:
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
        return ChargeBreakdown(
            brokerage=brokerage,
            stt=self._paisa(turnover * schedule.stt_sell_percent / _HUNDRED if selling else 0),
            exchange_transaction=transaction,
            sebi=sebi,
            stamp_duty=self._paisa(
                0 if selling else turnover * schedule.stamp_duty_buy_percent / _HUNDRED
            ),
            gst=self._paisa(
                (brokerage.amount + transaction.amount + sebi.amount)
                * schedule.gst_percent
                / _HUNDRED
            ),
        )

    @staticmethod
    def _paisa(amount: Decimal | int) -> Money:
        return Money(Decimal(amount).quantize(_PAISA, rounding=ROUND_HALF_UP))
