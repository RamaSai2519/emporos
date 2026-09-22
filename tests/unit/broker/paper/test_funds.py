"""Margin and funds: what is committed, what is free, what the open positions are worth."""

from __future__ import annotations

from decimal import Decimal

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerTrade
from emporos.broker.paper.account import PaperAccount
from emporos.broker.paper.funds import LastPrices, PaperFunds
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW
from tests.support.paper_rig import ID, order

ZERO = Money.zero()


class Working:
    def __init__(self, *orders: BrokerOrder) -> None:
        self._orders = orders

    def working_orders(self) -> list[BrokerOrder]:
        return list(self._orders)


def resting(qty: int, filled: int, price: str) -> BrokerOrder:
    return BrokerOrder(
        "O", "T", ID, OrderSide.BUY, OrderType.LIMIT, qty, filled,
        BrokerOrderStatus.PARTIALLY_FILLED, Money.of(price),
    )  # fmt: skip


def rig(cash: str = "10000", leverage: str = "1", working: Working | None = None):  # type: ignore[no-untyped-def]
    account, prices = PaperAccount(Money.of(cash)), LastPrices()
    return account, prices, PaperFunds(account, working or Working(), prices, Decimal(leverage))


def fill(account: PaperAccount, side: OrderSide, qty: int, price: str) -> None:
    account.apply(
        BrokerTrade(f"T{len(account.trades())}", "O", ID, side, qty, Money.of(price), NOW), ZERO
    )


def test_an_empty_account_has_all_its_cash_free() -> None:
    _, _, funds = rig()

    snapshot = funds.funds()

    assert (snapshot.net, snapshot.available_cash, snapshot.utilised) == (
        Money.of("10000"), Money.of("10000"), ZERO,
    )  # fmt: skip


def test_an_order_needs_notional_over_leverage_as_margin() -> None:
    _, _, funds = rig(leverage="5")

    assert funds.margin_required(order(qty=10, price="100.00")) == Money.of("200")


def test_a_sell_to_open_a_short_needs_margin_like_a_buy() -> None:
    _, _, funds = rig(leverage="5")

    short = order(side=OrderSide.SELL, qty=10, price="100.00")
    assert funds.margin_required(short) == Money.of("200")


def test_only_the_quantity_that_opens_a_position_needs_margin() -> None:
    account, _, funds = rig()
    fill(account, OrderSide.BUY, 10, "100.00")

    assert funds.margin_required(order(side=OrderSide.SELL, qty=10, price="100")) == ZERO
    assert funds.margin_required(order(side=OrderSide.SELL, qty=15, price="100")) == Money.of("500")
    assert funds.margin_required(order(side=OrderSide.BUY, qty=5, price="100")) == Money.of("500")


def test_open_positions_and_working_orders_both_commit_margin() -> None:
    account, _, funds = rig(working=Working(resting(qty=10, filled=4, price="50.00")))
    fill(account, OrderSide.BUY, 10, "100.00")

    assert funds.utilised() == Money.of("1000") + Money.of("300")  # position + 6 unfilled @ 50
    assert funds.available_cash() == Money.of("10000") - Money.of("1300")


def test_unrealised_pnl_marks_open_positions_to_the_last_price() -> None:
    account, prices, funds = rig()
    fill(account, OrderSide.BUY, 10, "100.00")
    assert funds.unrealised() == ZERO and funds.unrealised_for(ID) is None  # no price seen yet

    prices.observe(make_tick(NOW, "103.00", instrument_id=ID))

    assert funds.unrealised() == Money.of("30.00")
    assert funds.unrealised_for(ID) == Money.of("30.00")
    assert funds.funds().net == Money.of("10030.00")


def test_a_short_position_is_marked_the_other_way() -> None:
    account, prices, funds = rig()
    fill(account, OrderSide.SELL, 10, "100.00")
    prices.observe(make_tick(NOW, "103.00", instrument_id=ID))

    assert funds.unrealised() == Money.of("-30.00")


def test_out_of_order_ticks_do_not_move_the_mark() -> None:
    prices = LastPrices()
    prices.observe(make_tick(NOW, "100.00", instrument_id=ID))
    prices.observe(make_tick(NOW, "1.00", instrument_id=ID, out_of_order=True))

    assert prices.last(ID) == Money.of("100.00")


def test_leverage_below_one_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError, match="leverage"):
        rig(leverage="0.5")
