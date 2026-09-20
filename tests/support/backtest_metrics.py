"""Builders for metrics tests: equity points, daily series and closed trades with known numbers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from emporos.backtest.metrics.equity import DailyEquity
from emporos.backtest.portfolio import ClosedTrade, EquityPoint, TradeDirection
from emporos.domain.money import Money
from tests.support.strategies import INSTRUMENT, T0


def daily(start: str, equities: list[str], first: date = date(2026, 1, 5)) -> list[DailyEquity]:
    """Consecutive days whose returns follow from `equities`, the first measured from `start`."""
    days: list[DailyEquity] = []
    previous = Decimal(start)
    for offset, text in enumerate(equities):
        equity = Decimal(text)
        days.append(DailyEquity(first + timedelta(days=offset), equity, equity / previous - 1))
        previous = equity
    return days


def point(
    equity: str,
    when: datetime = T0,
    exposure: str = "0",
    open_positions: int = 0,
) -> EquityPoint:
    return EquityPoint(when, Money.of(equity), Money.of(exposure), open_positions)


def curve(equities: list[str], step: timedelta = timedelta(days=1)) -> list[EquityPoint]:
    return [point(e, T0 + step * n) for n, e in enumerate(equities, start=1)]


def trade(
    net: str, minute: int, fees: str = "0", quantity: int = 10, price: str = "100"
) -> ClosedTrade:
    """A LONG trade of `quantity` @ `price` (notional 1,000 by default) that netted `net`."""
    gross = Decimal(net) + Decimal(fees)
    return ClosedTrade(
        instrument_id=INSTRUMENT,
        direction=TradeDirection.LONG,
        quantity=quantity,
        opened_at=T0 + timedelta(minutes=minute),
        closed_at=T0 + timedelta(minutes=minute + 1),
        entry_price=Money.of(price),
        exit_price=Money(Decimal(price) + gross / quantity),
        gross_pnl=Money(gross),
        fees=Money.of(fees),
    )


UTC_NOON = datetime(2026, 1, 5, 6, 30, tzinfo=UTC)
