"""A snapshot or a position row must follow from the fill history — provably, not by trust."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.persistence.records import ExecutionRecord, PortfolioSnapshotRecord
from emporos.portfolio.ledger import PortfolioValuator, PositionCalculator
from emporos.portfolio.marks import LatestTickMarks
from emporos.portfolio.replay import PositionReplay, orders_disagreeing_with_fills
from emporos.portfolio.service import PortfolioService
from emporos.portfolio.snapshots import (
    SnapshotKind,
    SnapshotSchedule,
    SnapshotService,
    SnapshotVerifier,
)
from tests.support.records import NOW, RecordFactory

ACCOUNT = "acct"
DAY0 = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)  # 09:30 IST


def fill(n: int, side: OrderSide, qty: int, price: str, at: datetime) -> ExecutionRecord:
    return RecordFactory().execution(
        _id=f"e{n}", broker_trade_id=f"t{n}", order_id=f"o{n}", instrument_id="A", side=side,
        quantity=qty, price=Money.of(price), ts=at, account_id=ACCOUNT, fees=Money.of("1"),
        session_date="2026-09-18", session_trade_no=n,
    )  # fmt: skip


HISTORY = [
    fill(1, OrderSide.BUY, 10, "100", DAY0),
    fill(2, OrderSide.BUY, 10, "102", DAY0 + timedelta(minutes=1)),
    fill(3, OrderSide.SELL, 15, "103", DAY0 + timedelta(minutes=2)),
]


class TestPositionReplay:
    def test_replaying_the_history_gives_the_hand_computed_position(self) -> None:
        (position,) = PositionReplay(PositionCalculator()).replay(ACCOUNT, HISTORY).values()
        # average 101 after two buys; selling 15 at 103 realises 15 * 2 = 30 gross, 3 fees.
        assert (position.net_quantity, position.average_price) == (5, Money.of("101"))
        assert (position.gross_realised_pnl, position.fees) == (Money.of("30"), Money.of("3"))
        assert position.realised_pnl == Money.of("27")

    def test_a_stored_position_that_its_history_contradicts_is_named(self) -> None:
        replay = PositionReplay(PositionCalculator())
        good = replay.replay(ACCOUNT, HISTORY)
        assert replay.differences(good.values(), good) == []
        wrong = {"A": next(iter(good.values())).model_copy(update={"net_quantity": 6})}
        ((instrument, detail),) = replay.differences(wrong.values(), good)
        assert instrument == "A" and "stored" in detail
        assert replay.differences([], good)[0][0] == "A"  # a row is missing altogether

    def test_orders_are_checked_against_the_fills_that_belong_to_them(self) -> None:
        factory = RecordFactory()
        ok = factory.order(_id="o1", filled_quantity=10)
        bad = factory.order(_id="o2", filled_quantity=3)
        filled = PositionReplay.filled_by_order(HISTORY)
        assert filled["o1"] == 10
        assert [o for o, _ in orders_disagreeing_with_fills([ok, bad], filled)] == ["o2"]


class Cash:
    async def available_cash(self) -> Money | None:
        return Money.of("100000")


class Fills:
    def __init__(self, n: int) -> None:
        self.n = n

    async def fills_this_session(self, session_date: str) -> int:
        return self.n


class Sink:
    def __init__(self) -> None:
        self.records: list[PortfolioSnapshotRecord] = []

    async def insert(self, record: PortfolioSnapshotRecord) -> None:
        self.records.append(record)


class Positions:
    def __init__(self, records: list) -> None:  # type: ignore[type-arg]
        self.records = records

    async def all_positions(self) -> list:  # type: ignore[type-arg]
        return self.records

    async def open_positions(self) -> list:  # type: ignore[type-arg]
        return [p for p in self.records if p.net_quantity]


class TestSnapshots:
    async def _service(self, at: datetime) -> tuple[SnapshotService, Sink]:
        clock = FixedClock(at)
        replay = PositionReplay(PositionCalculator())
        positions = Positions(list(replay.replay(ACCOUNT, HISTORY).values()))
        marks = LatestTickMarks(clock)
        from emporos.domain.ticks import Tick

        marks.on_tick(Tick("A", at, at, Money.of("104"), 1))
        sink = Sink()
        service = SnapshotService(
            ACCOUNT, PortfolioService(positions, marks, PortfolioValuator()), Cash(),
            Fills(3), sink, clock,
        )  # fmt: skip
        return service, sink

    async def test_a_snapshot_records_positions_pnl_cash_and_trade_count(self) -> None:
        service, sink = await self._service(DAY0 + timedelta(minutes=5))
        snap = await service.take(SnapshotKind.INTRADAY)
        assert sink.records == [snap] and snap.kind == "INTRADAY"
        assert snap.session_date == "2026-09-18" and snap.trades == 3
        assert snap.cash == Money.of("100000")
        assert snap.realised_pnl == Money.of("27")
        assert snap.unrealised_pnl == Money.of("15")  # 5 * (104 - 101)
        (position,) = snap.positions
        assert (position.instrument_id, position.net_quantity) == ("A", 5)

    async def test_a_snapshot_reconstructs_from_the_fill_history_alone(self) -> None:
        service, _ = await self._service(DAY0 + timedelta(minutes=5))
        verifier = SnapshotVerifier(PositionReplay(PositionCalculator()))
        snap = await service.take(SnapshotKind.EOD)
        assert verifier.problems(snap, HISTORY) == []
        assert verifier.problems(snap, HISTORY[:2])  # a history missing the sell disagrees
        tampered = snap.model_copy(
            update={"positions": [snap.positions[0].model_copy(update={"net_quantity": 9})]}
        )
        assert "disagrees" in verifier.problems(tampered, HISTORY)[0]
        assert verifier.problems(snap.model_copy(update={"positions": []}), HISTORY)

    async def test_a_snapshot_before_a_later_fill_does_not_include_it(self) -> None:
        service, _ = await self._service(DAY0 + timedelta(minutes=5))
        verifier = SnapshotVerifier(PositionReplay(PositionCalculator()))
        snap = await service.take(SnapshotKind.INTRADAY)
        later = [*HISTORY, fill(4, OrderSide.BUY, 100, "90", DAY0 + timedelta(hours=1))]
        assert verifier.problems(snap, later) == []  # fills after the snapshot are not its business


class TestSchedule:
    schedule = SnapshotSchedule(interval=timedelta(minutes=5), eod_at=time(15, 35))

    def test_intraday_snapshots_follow_the_interval(self) -> None:
        now = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)  # 09:30 IST
        assert self.schedule.due(now, None, False) is SnapshotKind.INTRADAY
        assert self.schedule.due(now, now - timedelta(minutes=4), False) is None
        assert self.schedule.due(now, now - timedelta(minutes=5), False) is SnapshotKind.INTRADAY

    def test_the_end_of_day_snapshot_is_taken_once_after_the_close(self) -> None:
        late = datetime(2026, 9, 18, 10, 6, tzinfo=UTC)  # 15:36 IST
        assert self.schedule.due(late, late - timedelta(hours=1), False) is SnapshotKind.EOD
        assert self.schedule.due(late, late - timedelta(hours=1), True) is None

    def test_the_interval_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SnapshotSchedule(interval=timedelta(0))
        assert Decimal(1)
        assert NOW
