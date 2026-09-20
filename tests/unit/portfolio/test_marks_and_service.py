"""Live marks and the portfolio view: P&L that is real, or explicitly absent — never invented."""

from datetime import timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.persistence.records import PositionRecord
from emporos.portfolio.ledger import PortfolioValuator
from emporos.portfolio.marks import LatestTickMarks
from emporos.portfolio.service import PortfolioService
from tests.support.records import NOW, RecordFactory


def tick(instrument: str, price: str, seconds: int = 0, *, out_of_order: bool = False) -> Tick:
    at = NOW + timedelta(seconds=seconds)
    return Tick(instrument, at, at, Money.of(price), seconds, out_of_order=out_of_order)


class TestLatestTickMarks:
    def test_the_latest_in_order_tick_is_the_mark(self) -> None:
        marks = LatestTickMarks(FixedClock(NOW))
        marks.on_tick(tick("A", "100", 0))
        marks.on_tick(tick("A", "101", 1))
        marks.on_tick(tick("A", "99", 0))  # older than what we hold
        marks.on_tick(tick("A", "50", 2, out_of_order=True))
        assert marks.marks() == {"A": Money.of("101")}

    def test_a_stale_mark_is_withheld(self) -> None:
        clock = FixedClock(NOW)
        marks = LatestTickMarks(clock, max_age=timedelta(seconds=30))
        marks.on_tick(tick("A", "100"))
        clock.advance(timedelta(seconds=30))
        assert "A" in marks.marks()
        clock.advance(timedelta(seconds=1))
        assert marks.marks() == {}

    def test_a_mark_needs_a_positive_lifetime(self) -> None:
        with pytest.raises(ValueError):
            LatestTickMarks(FixedClock(NOW), max_age=timedelta(0))


class Positions:
    def __init__(self, records: list[PositionRecord]) -> None:
        self.records = records

    async def all_positions(self) -> list[PositionRecord]:
        return self.records

    async def open_positions(self) -> list[PositionRecord]:
        return [p for p in self.records if p.net_quantity]


class Marks:
    def __init__(self, marks: dict[str, Money]) -> None:
        self._marks = marks

    def marks(self) -> dict[str, Money]:
        return self._marks


class TestPortfolioService:
    def _records(self) -> list[PositionRecord]:
        f = RecordFactory()
        return [
            f.position(instrument_id="A", net_quantity=10, average_price=Money.of("100"),
                       realised_pnl=Money.of("40"), fees=Money.of("3")),
            f.position(instrument_id="B", net_quantity=-5, average_price=Money.of("200"),
                       realised_pnl=Money.zero(), fees=Money.of("2")),
            f.position(instrument_id="C", net_quantity=0, average_price=Money.zero(),
                       realised_pnl=Money.of("-10"), fees=Money.of("1")),
        ]  # fmt: skip

    async def test_unrealised_pnl_follows_the_marks_and_matches_hand_arithmetic(self) -> None:
        marks = {"A": Money.of("103"), "B": Money.of("198")}
        service = PortfolioService(Positions(self._records()), Marks(marks), PortfolioValuator())
        view = await service.view()
        by = {p.instrument_id: p for p in view.positions}
        assert by["A"].unrealised_pnl == Money.of("30")  # 10 * (103 - 100)
        assert by["B"].unrealised_pnl == Money.of("10")  # short 5 * (198 - 200) => +10
        assert by["C"].unrealised_pnl == Money.zero() and by["C"].mark is None
        assert view.valuation.unrealised == Money.of("40")
        assert view.valuation.realised == Money.of("30")  # 40 + 0 - 10
        assert view.valuation.fees == Money.of("6")
        assert view.gross_exposure == Money.of("1030") + Money.of("990")  # 10*103 + 5*198

    async def test_pnl_updates_as_the_price_moves(self) -> None:
        marks = Marks({"A": Money.of("101"), "B": Money.of("200")})
        service = PortfolioService(Positions(self._records()), marks, PortfolioValuator())
        first = (await service.view()).valuation.unrealised
        marks._marks["A"] = Money.of("105")
        second = (await service.view()).valuation.unrealised
        assert (first, second) == (Money.of("10"), Money.of("50"))

    async def test_a_missing_mark_makes_the_totals_unknown_not_zero(self) -> None:
        service = PortfolioService(
            Positions(self._records()), Marks({"A": Money.of("103")}), PortfolioValuator()
        )
        view = await service.view()
        assert view.valuation.unrealised is None and view.valuation.missing_marks == ("B",)
        assert view.gross_exposure is None
        assert {p.instrument_id: p.unrealised_pnl for p in view.positions}["B"] is None
