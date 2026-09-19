"""Position, P&L and cash arithmetic — exact Decimal, derived from trades alone."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.broker.models import BrokerTrade
from emporos.broker.paper.account import PaperAccount
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

T = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
ID = "NSE:3045"
ZERO = Money.zero()
_ids = iter(range(1, 1000))


def trade(side: OrderSide, qty: int, price: str) -> BrokerTrade:
    return BrokerTrade(f"T{next(_ids)}", "O1", ID, side, qty, Money.of(price), T)


def test_buying_opens_a_long_at_the_weighted_average_price() -> None:
    account = PaperAccount(Money.of("100000"))

    account.apply(trade(OrderSide.BUY, 10, "100.00"), ZERO)
    position = account.apply(trade(OrderSide.BUY, 30, "104.00"), ZERO)

    assert (position.net_quantity, position.average_price) == (40, Money.of("103.00"))
    assert position.gross_realised == ZERO


def test_selling_part_of_a_long_books_profit_and_keeps_the_average_cost() -> None:
    account = PaperAccount(Money.of("100000"))
    account.apply(trade(OrderSide.BUY, 10, "100.00"), ZERO)

    position = account.apply(trade(OrderSide.SELL, 4, "103.00"), ZERO)

    assert (position.net_quantity, position.average_price) == (6, Money.of("100.00"))
    assert position.gross_realised == Money.of("12.00")


def test_closing_flat_resets_the_average_and_keeps_the_profit() -> None:
    account = PaperAccount(Money.of("100000"))
    account.apply(trade(OrderSide.BUY, 10, "100.00"), ZERO)

    position = account.apply(trade(OrderSide.SELL, 10, "98.00"), ZERO)

    assert (position.net_quantity, position.average_price) == (0, ZERO)
    assert position.gross_realised == Money.of("-20.00")


def test_a_short_profits_when_price_falls() -> None:
    account = PaperAccount(Money.of("100000"))
    account.apply(trade(OrderSide.SELL, 10, "100.00"), ZERO)

    position = account.apply(trade(OrderSide.BUY, 4, "97.00"), ZERO)

    assert (position.net_quantity, position.average_price) == (-6, Money.of("100.00"))
    assert position.gross_realised == Money.of("12.00")


def test_flipping_through_zero_closes_the_old_side_and_opens_the_new_one_at_the_fill_price() -> (
    None
):
    account = PaperAccount(Money.of("100000"))
    account.apply(trade(OrderSide.BUY, 10, "100.00"), ZERO)

    position = account.apply(trade(OrderSide.SELL, 15, "102.00"), ZERO)

    assert (position.net_quantity, position.average_price) == (-5, Money.of("102.00"))
    assert position.gross_realised == Money.of("20.00")  # only the 10 that closed


def test_charges_reduce_realised_pnl_and_cash_but_not_the_gross_figure() -> None:
    account = PaperAccount(Money.of("1000"))
    account.apply(trade(OrderSide.BUY, 10, "100.00"), Money.of("1.50"))
    position = account.apply(trade(OrderSide.SELL, 10, "101.00"), Money.of("1.75"))

    assert position.gross_realised == Money.of("10.00")
    assert position.fees == Money.of("3.25") and position.realised == Money.of("6.75")
    assert (account.realised, account.fees) == (Money.of("6.75"), Money.of("3.25"))
    assert account.cash == Money.of("1006.75")


def test_the_trade_book_and_positions_are_copies() -> None:
    account = PaperAccount(Money.of("1000"))
    account.apply(trade(OrderSide.BUY, 1, "100"), ZERO)

    account.trades().clear()
    account.positions().clear()

    assert len(account.trades()) == 1 and len(account.positions()) == 1
    assert account.position("NSE:nothing") is None


def test_average_prices_that_do_not_divide_evenly_stay_exact_decimals() -> None:
    account = PaperAccount(Money.of("100000"))
    account.apply(trade(OrderSide.BUY, 1, "100.00"), ZERO)

    position = account.apply(trade(OrderSide.BUY, 2, "100.01"), ZERO)

    assert isinstance(position.average_price.amount, Decimal)
    assert (position.average_price.amount * 3).quantize(Decimal("0.0001")) == Decimal("300.0200")


def test_money_that_makes_no_sense_is_refused() -> None:
    with pytest.raises(ValueError, match="cash"):
        PaperAccount(Money.of("-1"))
    with pytest.raises(ValueError, match="charges"):
        PaperAccount(Money.of("1")).apply(trade(OrderSide.BUY, 1, "1"), Money.of("-0.01"))
