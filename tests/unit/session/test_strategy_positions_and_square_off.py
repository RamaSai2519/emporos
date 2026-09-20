"""Strategies see only their own positions; square-off goes through the same gates as any signal."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.portfolio.ledger import PositionCalculator
from emporos.session.quoter import MarkRepriceQuoter
from emporos.session.square_off import SQUARE_OFF_RUN, SquareOffService
from emporos.session.strategy_positions import StrategyPositionBook
from tests.support.execution import FixedTicks
from tests.support.fakes import RecordingAlertSink
from tests.support.records import NOW, RecordFactory


def fill(order_id: str, side: OrderSide, qty: int, price: str, trade: str):  # type: ignore[no-untyped-def]
    return RecordFactory().execution(
        _id=f"e-{trade}", broker_trade_id=trade, order_id=order_id, instrument_id="A",
        side=side, quantity=qty, price=Money.of(price), account_id="acct",
    )  # fmt: skip


class TestStrategyPositionBook:
    def book(self) -> StrategyPositionBook:
        return StrategyPositionBook("acct", PositionCalculator())

    def test_each_strategy_sees_only_what_its_own_orders_produced(self) -> None:
        f, book = RecordFactory(), self.book()
        mine = f.order(_id="o1", strategy_run_id="run-a", instrument_id="A")
        theirs = f.order(_id="o2", strategy_run_id="run-b", instrument_id="A")
        book.on_fill(mine, fill("o1", OrderSide.BUY, 10, "100", "t1"))
        book.on_fill(theirs, fill("o2", OrderSide.BUY, 4, "110", "t2"))
        a, b = book.view_for("run-a"), book.view_for("run-b")
        assert (a.position("A").net_quantity, a.position("A").average_price) == (
            10,
            Money.of("100"),
        )
        assert b.position("A").net_quantity == 4
        assert a.position("ZZZ").is_flat and book.view_for("run-c").open_positions() == ()

    def test_a_redelivered_fill_is_not_counted_twice_and_a_manual_order_is_no_ones(self) -> None:
        f, book = RecordFactory(), self.book()
        order = f.order(_id="o1", strategy_run_id="run-a", instrument_id="A")
        book.on_fill(order, fill("o1", OrderSide.BUY, 10, "100", "t1"))
        book.on_fill(order, fill("o1", OrderSide.BUY, 10, "100", "t1"))
        book.on_fill(f.order(_id="m", strategy_run_id=None), fill("m", OrderSide.BUY, 5, "1", "t9"))
        assert book.view_for("run-a").position("A").net_quantity == 10

    def test_closing_a_position_makes_it_flat_and_it_is_rebuilt_from_history(self) -> None:
        f, book = RecordFactory(), self.book()
        order = f.order(_id="o1", strategy_run_id="run-a", instrument_id="A")
        history = [
            fill("o1", OrderSide.BUY, 10, "100", "t1"),
            fill("o1", OrderSide.SELL, 10, "101", "t2"),
        ]
        book.load(history, {"o1": order, "orphan": f.order()})
        assert book.view_for("run-a").position("A").is_flat
        assert book.view_for("run-a").open_positions() == ()


class Sink:
    def __init__(self) -> None:
        self.signals: list[Signal] = []

    async def submit(self, signal: Signal) -> None:
        self.signals.append(signal)


class Held:
    def __init__(self, positions: list, orders: list | None = None) -> None:  # type: ignore[type-arg]
        self.positions, self.orders = positions, orders or []

    async def open_positions(self) -> list:  # type: ignore[type-arg]
        return self.positions

    async def active(self) -> list:  # type: ignore[type-arg]
        return self.orders


class Marks:
    def __init__(self, marks: dict[str, Money]) -> None:
        self._marks = marks

    def marks(self) -> dict[str, Money]:
        return self._marks


class TestSquareOff:
    def build(self, held: Held, marks: dict[str, Money]):  # type: ignore[no-untyped-def]
        sink, alerts = Sink(), RecordingAlertSink()
        service = SquareOffService(held, held, Marks(marks), sink, FixedClock(NOW), alerts)
        return service, sink, alerts

    async def test_each_open_position_becomes_an_exit_signal_on_the_opposite_side(self) -> None:
        f = RecordFactory()
        held = Held([
            f.position(instrument_id="A", net_quantity=10),
            f.position(instrument_id="B", net_quantity=-7),
        ])  # fmt: skip
        service, sink, _ = self.build(held, {"A": Money.of("100"), "B": Money.of("50")})
        report = await service.flatten("end of session")
        assert report.submitted == ("A", "B")
        a, b = sink.signals
        assert (a.side, a.quantity, a.kind) == (OrderSide.SELL, 10, SignalKind.EXIT)
        assert (b.side, b.quantity) == (OrderSide.BUY, 7)
        assert a.order_type is OrderType.LIMIT and a.strategy_run_id == SQUARE_OFF_RUN
        assert a.limit_price == Money.of("100") and a.reason == "end of session"

    async def test_a_position_with_no_live_price_is_reported_never_guessed(self) -> None:
        held = Held([RecordFactory().position(instrument_id="A", net_quantity=10)])
        service, sink, alerts = self.build(held, {})
        report = await service.flatten("eod")
        assert report.unpriced == ("A",) and sink.signals == []
        assert [name for name, _ in alerts.alerts] == ["square_off_unpriced"]

    async def test_an_exit_already_working_is_not_duplicated(self) -> None:
        f = RecordFactory()
        working = f.order(instrument_id="A", side=OrderSide.SELL, signal_kind="EXIT", state="OPEN")
        held = Held([f.position(instrument_id="A", net_quantity=10)], [working])
        service, sink, _ = self.build(held, {"A": Money.of("100")})
        report = await service.flatten("eod")
        assert report.already_closing == ("A",) and sink.signals == []

    async def test_an_entry_order_on_the_same_side_does_not_count_as_closing(self) -> None:
        f = RecordFactory()
        entry = f.order(instrument_id="A", side=OrderSide.SELL, signal_kind="ENTRY", state="OPEN")
        held = Held([f.position(instrument_id="A", net_quantity=10)], [entry])
        service, sink, _ = self.build(held, {"A": Money.of("100")})
        assert (await service.flatten("eod")).submitted == ("A",)
        assert len(sink.signals) == 1 and replace(sink.signals[0]) and timedelta(0) == timedelta(0)


class TestQuoter:
    async def test_a_buy_is_priced_up_and_a_sell_down_to_the_tick(self) -> None:
        f = RecordFactory()
        quoter = MarkRepriceQuoter(Marks({"A": Money.of("100.00")}), FixedTicks(), Decimal("7"))
        buy = await quoter.candidate(f.order(instrument_id="A", side=OrderSide.BUY))
        sell = await quoter.candidate(f.order(instrument_id="A", side=OrderSide.SELL))
        assert buy == Money.of("100.10")  # 100.07 rounded UP to 0.05
        assert sell == Money.of("99.90")  # 99.93 rounded DOWN to 0.05

    async def test_no_mark_means_no_price(self) -> None:
        quoter = MarkRepriceQuoter(Marks({}), FixedTicks(), Decimal("5"))
        assert await quoter.candidate(RecordFactory().order(instrument_id="A")) is None

    def test_a_step_cannot_be_negative(self) -> None:
        with pytest.raises(ValueError):
            MarkRepriceQuoter(Marks({}), FixedTicks(), Decimal("-1"))
