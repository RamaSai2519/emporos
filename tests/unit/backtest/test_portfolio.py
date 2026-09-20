"""EM-104: portfolio accounting worked by hand, plus invariants that hold for any fills."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emporos.backtest.orders import Fill, FillReason
from emporos.backtest.portfolio import BacktestPortfolio, TradeDirection
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0

BUY, SELL = OrderSide.BUY, OrderSide.SELL


def money(value: str) -> Money:
    return Money.of(value)


class Book:
    """A portfolio plus a way to book fills one after another, minutes apart."""

    def __init__(self, cash: str = "100000") -> None:
        self.portfolio = BacktestPortfolio(money(cash))
        self._n = 0

    def fill(
        self,
        side: OrderSide,
        quantity: int,
        price: str,
        fee: str = "0",
        instrument: str = INSTRUMENT,
    ) -> None:
        self._n += 1
        when: datetime = T0 + timedelta(minutes=5 * self._n)
        fill = Fill(self._n, f"SIM-{self._n}", f"t{self._n}", instrument, side, quantity,
                    money(price), when, FillReason.MATCHED)  # fmt: skip
        self.portfolio.apply(fill, money(fee))


class TestARoundTripByHand:
    def test_the_contract_note_round_trip(self) -> None:
        # BUY 100 @ 500 (charges 26.96), SELL 100 @ 505 (charges 38.12)
        book = Book()
        book.fill(BUY, 100, "500", "26.96")
        book.fill(SELL, 100, "505", "38.12")
        p = book.portfolio

        (trade,) = p.closed_trades
        assert (trade.direction, trade.quantity) == (TradeDirection.LONG, 100)
        assert (trade.entry_price, trade.exit_price) == (money("500"), money("505"))
        assert trade.gross_pnl == money("500")  # (505 - 500) * 100
        assert trade.fees == money("65.08")  # 26.96 + 38.12
        assert trade.net_pnl == money("434.92")
        assert (trade.opened_at, trade.closed_at) == (
            T0 + timedelta(minutes=5),
            T0 + timedelta(minutes=10),
        )
        assert p.position(INSTRUMENT).is_flat and p.open_positions() == ()
        assert (p.realised, p.fees, p.unrealised) == (money("434.92"), money("65.08"), money("0"))
        assert p.equity() == money("100434.92")

    def test_scaling_in_averages_the_entry_price(self) -> None:
        # BUY 50 @ 100, BUY 50 @ 110 -> 100 @ 105; SELL 100 @ 120 -> (120 - 105) * 100 = 1500
        book = Book()
        book.fill(BUY, 50, "100")
        book.fill(BUY, 50, "110")
        assert book.portfolio.position(INSTRUMENT).average_price == money("105")
        book.fill(SELL, 100, "120")

        (trade,) = book.portfolio.closed_trades
        assert (trade.quantity, trade.entry_price, trade.exit_price) == (
            100,
            money("105"),
            money("120"),
        )
        assert trade.gross_pnl == money("1500")

    def test_scaling_out_is_one_trade_with_the_average_exit(self) -> None:
        # BUY 100 @ 100; SELL 40 @ 110 (+400) then SELL 60 @ 105 (+300); exit avg (4400 + 6300)/100
        book = Book()
        book.fill(BUY, 100, "100")
        book.fill(SELL, 40, "110")
        assert book.portfolio.closed_trades == ()  # still open: not a finished trade yet
        assert book.portfolio.position(INSTRUMENT).net_quantity == 60
        book.fill(SELL, 60, "105")

        (trade,) = book.portfolio.closed_trades
        assert trade.gross_pnl == money("700")
        assert trade.exit_price == money("107")

    def test_a_short_round_trip(self) -> None:
        book = Book()
        book.fill(SELL, 10, "200")
        assert book.portfolio.position(INSTRUMENT).net_quantity == -10
        book.fill(BUY, 10, "190")

        (trade,) = book.portfolio.closed_trades
        assert trade.direction is TradeDirection.SHORT
        assert (trade.entry_price, trade.exit_price, trade.gross_pnl) == (
            money("200"), money("190"), money("100"),
        )  # fmt: skip

    def test_a_losing_trade_has_negative_net_pnl(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100", "5")
        book.fill(SELL, 10, "99", "5")
        (trade,) = book.portfolio.closed_trades
        assert trade.gross_pnl == money("-10") and trade.net_pnl == money("-20")


class TestSeveralTripsInOneInstrument:
    def test_each_trip_carries_only_its_own_profit_and_charges(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100", "3")
        book.fill(SELL, 10, "102", "4")  # trip 1: gross 20, fees 7
        book.fill(BUY, 10, "100", "5")
        book.fill(SELL, 10, "99", "6")  # trip 2: gross -10, fees 11

        first, second = book.portfolio.closed_trades
        assert (first.gross_pnl, first.fees) == (money("20"), money("7"))
        assert (second.gross_pnl, second.fees) == (money("-10"), money("11"))
        assert book.portfolio.realised == money("10") - money("18")


class TestAFlip:
    def test_a_fill_that_reverses_the_position_closes_one_trade_and_opens_the_next(self) -> None:
        # BUY 10 @ 100 (fee 3); SELL 25 @ 110 (fee 4): closes the long (+100), opens a 15 short;
        # BUY 15 @ 105 (fee 5): the short gains 15 * 5 = 75
        book = Book()
        book.fill(BUY, 10, "100", "3")
        book.fill(SELL, 25, "110", "4")
        assert book.portfolio.position(INSTRUMENT).net_quantity == -15
        book.fill(BUY, 15, "105", "5")

        long, short = book.portfolio.closed_trades
        assert (long.direction, long.quantity, long.gross_pnl, long.fees) == (
            TradeDirection.LONG, 10, money("100"), money("7"),
        )  # fmt: skip  # the flipping fill's fee belongs to the trade it closed
        assert (short.direction, short.quantity, short.gross_pnl, short.fees) == (
            TradeDirection.SHORT, 15, money("75"), money("5"),
        )  # fmt: skip
        assert (short.entry_price, short.exit_price) == (money("110"), money("105"))
        assert book.portfolio.fees == money("12")

    def test_the_exit_average_counts_only_the_part_of_a_flipping_fill_that_closed(self) -> None:
        # BUY 10 @ 100; SELL 5 @ 105; SELL 25 @ 110 closes the last 5 of the long (and opens a
        # 20 short). The long's exit average is (5*105 + 5*110) / 10 = 107.5, not counting the 20.
        book = Book()
        book.fill(BUY, 10, "100")
        book.fill(SELL, 5, "105")
        book.fill(SELL, 25, "110")

        (long,) = book.portfolio.closed_trades
        assert long.exit_price == money("107.5")
        assert long.gross_pnl == money("75")  # 5*5 + 5*10
        assert book.portfolio.position(INSTRUMENT).net_quantity == -20


class TestValuation:
    def test_a_fill_refreshes_the_mark_it_does_not_leave_a_stale_one(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100")
        book.portfolio.mark(INSTRUMENT, money("103"))  # a bar closed at 103...
        book.fill(BUY, 10, "105")  # ...then more filled at 105: the freshest price we know

        # average 102.5 on 20 shares, marked at 105: 20 * 2.5
        assert book.portfolio.unrealised == money("50")

    def test_an_open_long_is_marked_to_the_latest_price(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100", "2")
        assert book.portfolio.unrealised == money("0")  # marked at its own fill price
        book.portfolio.mark(INSTRUMENT, money("103"))

        p = book.portfolio
        assert p.unrealised == money("30")
        assert p.gross_exposure() == money("1030")
        assert p.equity() == money("100000") + money("-2") + money("30")

    def test_an_open_short_gains_when_the_price_falls(self) -> None:
        book = Book()
        book.fill(SELL, 10, "100")
        book.portfolio.mark(INSTRUMENT, money("97"))
        assert book.portfolio.unrealised == money("30")
        assert book.portfolio.gross_exposure() == money("970")

    def test_each_instrument_is_marked_on_its_own(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100")
        book.fill(BUY, 20, "50", instrument=OTHER_INSTRUMENT)
        book.portfolio.mark(INSTRUMENT, money("101"))
        book.portfolio.mark(OTHER_INSTRUMENT, money("49"))

        assert book.portfolio.unrealised == money("10") + money("-20")
        assert {p.instrument_id for p in book.portfolio.open_positions()} == {
            INSTRUMENT,
            OTHER_INSTRUMENT,
        }

    def test_the_equity_curve_holds_the_points_the_caller_recorded(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100")
        book.portfolio.mark(INSTRUMENT, money("102"))
        first = book.portfolio.record_point(T0)
        book.portfolio.mark(INSTRUMENT, money("98"))
        book.portfolio.record_point(T0 + timedelta(minutes=5))

        assert [
            (pt.equity, pt.gross_exposure, pt.open_positions) for pt in book.portfolio.equity_curve
        ] == [
            (money("100020"), money("1020"), 1),
            (money("99980"), money("980"), 1),
        ]
        assert first.ts == T0

    def test_traded_notional_and_fill_count(self) -> None:
        book = Book()
        book.fill(BUY, 10, "100")
        book.fill(SELL, 10, "101")
        assert book.portfolio.traded_notional == money("2010")
        assert book.portfolio.fill_count == 2


class TestAsAPositionView:
    def test_it_reports_flat_for_an_instrument_it_never_traded(self) -> None:
        assert BacktestPortfolio(money("1000")).position(INSTRUMENT).is_flat

    def test_the_strategy_sees_the_average_price_and_quantity(self) -> None:
        book = Book()
        book.fill(BUY, 7, "100")
        held = book.portfolio.position(INSTRUMENT)
        assert (held.net_quantity, held.average_price, held.is_long) == (7, money("100"), True)

    def test_starting_cash_must_be_positive(self) -> None:
        for cash in ("0", "-1"):
            with pytest.raises(ValueError):
                BacktestPortfolio(money(cash))


fills_strategy = st.lists(
    st.tuples(
        st.sampled_from([BUY, SELL]),
        st.integers(min_value=1, max_value=60),
        st.decimals(min_value=Decimal("90"), max_value=Decimal("110"), places=2),
        st.decimals(min_value=Decimal("0"), max_value=Decimal("9"), places=2),
    ),
    min_size=1,
    max_size=40,
)


@settings(max_examples=200, deadline=None)
@given(fills=fills_strategy)
def test_whatever_the_fills_are_the_books_balance_when_everything_is_closed_out(
    fills: list[tuple[OrderSide, int, Decimal, Decimal]],
) -> None:
    book = Book("1000000")
    net = 0
    for side, quantity, price, fee in fills:
        book.fill(side, quantity, str(price), str(fee))
        net += quantity if side is BUY else -quantity
    if net:  # close whatever is left at a final price
        book.fill(SELL if net > 0 else BUY, abs(net), "100")
    p = book.portfolio

    # cash flow computed independently of the portfolio: what came in, went out, was charged
    bought = sum(price * q for side, q, price, _ in fills if side is BUY)
    sold = sum(price * q for side, q, price, _ in fills if side is SELL)
    if net:
        bought += Decimal(100) * abs(net) if net < 0 else Decimal(0)
        sold += Decimal(100) * abs(net) if net > 0 else Decimal(0)
    charged = sum(fee for *_, fee in fills)

    # Average-cost arithmetic carries 28 significant digits, so a non-terminating average (100
    # and 101 in a 1:2 ratio) can leave dust far below a paisa; the books balance to within that.
    dust = Decimal("1e-15")
    assert p.open_positions() == ()
    assert p.unrealised == money("0")
    assert abs(p.equity().amount - (Decimal(1_000_000) + sold - bought - charged)) < dust
    assert (
        abs(sum((t.net_pnl for t in p.closed_trades), Money.zero()).amount - p.realised.amount)
        < dust
    )
    assert abs(sum((t.fees for t in p.closed_trades), Money.zero()).amount - p.fees.amount) < dust
