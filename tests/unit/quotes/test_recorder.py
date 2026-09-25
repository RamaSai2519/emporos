"""EM-217: the L1 quote recorder: batches, the recording window, order priority, back-off on a rate
limit, staleness and the flush cadence, with a scripted source and an in-memory sink."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.broker.errors import BrokerRateLimitedError, BrokerTransportError
from emporos.broker.models import Quote
from emporos.core.clock import IST, FixedClock
from emporos.domain.money import Money
from emporos.quotes.recorder import MAX_BATCH, QuoteRecorder, RecorderSettings
from emporos.quotes.row import QuoteRow

OPEN = datetime(2026, 9, 25, 10, 0, tzinfo=IST)  # a Friday inside the window


def quote(instrument: str, at: datetime, bid: str | None = "99.95", **kw: object) -> Quote:
    return Quote(
        instrument, Money.of("100"), Money.of("99"), Money.of("101"), Money.of("98"),
        Money.of("99.5"), 1000, at.astimezone(UTC),
        bid=Money.of(bid) if bid else None, ask=Money.of("100.05"), bid_qty=10, ask_qty=20,
        **kw,  # type: ignore[arg-type]
    )  # fmt: skip


class Source:
    """Answers each batch with one fresh quote per id, or raises what the script says."""

    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.calls: list[list[str]] = []
        self.raises: list[BaseException | None] = []
        self.stale: set[str] = set()
        self.no_time: set[str] = set()
        self.delay = 0.0
        self.tick = timedelta(0)

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        self.calls.append(list(instrument_ids))
        self.clock.advance(self.tick)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises and (error := self.raises.pop(0)) is not None:
            raise error
        out = []
        for i in instrument_ids:
            when = self.clock.now() - timedelta(days=1) if i in self.stale else self.clock.now()
            q = quote(i, when)
            if i in self.no_time:
                q = Quote(**{**q.__dict__, "exchange_ts": None})
            out.append(q)
        return out


class Sink:
    def __init__(self) -> None:
        self.rows: list[QuoteRow] = []
        self.flushed = 0
        self._pending = 0

    def append(self, rows: Sequence[QuoteRow]) -> None:
        self.rows.extend(rows)
        self._pending += len(rows)

    def flush(self) -> int:
        n, self._pending = self._pending, 0
        self.flushed += 1 if n else 0
        return n


class Orders:
    def __init__(self) -> None:
        self.pending = False
        self.after_calls: Callable[[], None] | None = None

    def orders_pending(self) -> bool:
        return self.pending


def rig(
    ids: list[str], *, settings: RecorderSettings | None = None, at: datetime = OPEN
) -> tuple[QuoteRecorder, Source, Sink, FixedClock, Orders]:
    clock = FixedClock(at)
    source, sink, orders = Source(clock), Sink(), Orders()
    recorder = QuoteRecorder(
        source, sink, ids, settings or RecorderSettings(), clock, priority=orders
    )
    return recorder, source, sink, clock, orders


IDS = ["NSE:1", "NSE:2", "NSE:3"]


class TestRecording:
    async def test_a_poll_records_one_row_per_instrument_with_the_receive_time(self) -> None:
        recorder, source, sink, clock, _ = rig(IDS)

        await recorder.poll()

        assert source.calls == [IDS]
        assert [r.instrument_id for r in sink.rows] == IDS
        row = sink.rows[0]
        assert row.received_at == clock.now() and row.ltp == Decimal("100")
        assert (row.bid, row.ask, row.bid_qty, row.ask_qty) == (
            Decimal("99.95"), Decimal("100.05"), 10, 20,
        )  # fmt: skip
        assert recorder.counters.polls == 1 and recorder.counters.rows == 3

    async def test_batches_never_exceed_fifty_symbols(self) -> None:
        ids = [f"NSE:{n}" for n in range(120)]
        recorder, source, sink, _, _ = rig(ids)

        await recorder.poll()

        assert [len(c) for c in source.calls] == [50, 50, 20]
        assert len(sink.rows) == 120

    async def test_a_duplicate_id_is_polled_once(self) -> None:
        recorder, source, _, _, _ = rig(["NSE:1", "NSE:1", "NSE:2"])

        await recorder.poll()

        assert source.calls == [["NSE:1", "NSE:2"]]
        assert recorder.instrument_ids == ("NSE:1", "NSE:2")

    async def test_an_empty_side_of_the_book_is_recorded_as_none(self) -> None:
        recorder, source, sink, _, _ = rig(["NSE:1"])

        async def one_sided(ids: Sequence[str]) -> list[Quote]:
            return [quote("NSE:1", OPEN, bid=None)]

        source.get_quote = one_sided  # type: ignore[method-assign]
        await recorder.poll()

        assert sink.rows[0].bid is None and sink.rows[0].ask == Decimal("100.05")

    async def test_quotes_not_dated_today_are_dropped_and_counted(self) -> None:
        recorder, source, sink, _, _ = rig(IDS)
        source.stale = {"NSE:2"}

        await recorder.poll()

        assert [r.instrument_id for r in sink.rows] == ["NSE:1", "NSE:3"]
        assert recorder.counters.stale_dropped == 1

    async def test_a_quote_with_no_exchange_time_is_kept_and_counted(self) -> None:
        recorder, source, sink, _, _ = rig(IDS)
        source.no_time = {"NSE:1"}

        await recorder.poll()

        assert len(sink.rows) == 3 and recorder.counters.no_exchange_time == 1


class TestTheWindow:
    @pytest.mark.parametrize(
        "moment",
        [
            datetime(2026, 9, 25, 9, 14, tzinfo=IST),
            datetime(2026, 9, 25, 15, 30, tzinfo=IST),
            datetime(2026, 9, 26, 11, 0, tzinfo=IST),  # a Saturday
        ],
    )
    async def test_nothing_is_polled_outside_the_session(self, moment: datetime) -> None:
        recorder, source, _, _, _ = rig(IDS, at=moment)

        await recorder.poll()

        assert source.calls == [] and recorder.counters.polls == 0

    @pytest.mark.parametrize(
        "moment",
        [datetime(2026, 9, 25, 9, 15, tzinfo=IST), datetime(2026, 9, 25, 15, 29, tzinfo=IST)],
    )
    async def test_the_edges_inside_the_session_are_polled(self, moment: datetime) -> None:
        recorder, source, _, _, _ = rig(IDS, at=moment)

        await recorder.poll()

        assert len(source.calls) == 1

    async def test_what_is_buffered_is_flushed_when_the_session_ends(self) -> None:
        recorder, _, sink, clock, _ = rig(IDS)
        await recorder.poll()
        assert sink.flushed == 0  # fewer polls than the flush cadence

        clock.set(datetime(2026, 9, 25, 15, 31, tzinfo=IST))
        await recorder.poll()

        assert sink.flushed == 1


class TestFlushCadence:
    async def test_the_sink_is_flushed_every_n_polls(self) -> None:
        recorder, _, sink, clock, _ = rig(IDS, settings=RecorderSettings(flush_every_polls=3))

        for _ in range(7):
            await recorder.poll()
            clock.advance(timedelta(minutes=1))

        assert sink.flushed == 2

    async def test_close_writes_what_is_left(self) -> None:
        recorder, _, sink, _, _ = rig(IDS)
        await recorder.poll()

        assert recorder.close() == 3 and sink.flushed == 1


class TestOrdersComeFirst:
    async def test_no_poll_while_an_order_is_in_flight(self) -> None:
        recorder, source, _, _, orders = rig(IDS)
        orders.pending = True

        await recorder.poll()

        assert source.calls == [] and recorder.counters.orders_yielded == 1

    async def test_an_order_arriving_mid_poll_stops_the_remaining_batches(self) -> None:
        recorder, source, sink, _, orders = rig([f"NSE:{n}" for n in range(120)])
        real = source.get_quote

        async def then_an_order(ids: Sequence[str]) -> list[Quote]:
            result = await real(ids)
            orders.pending = True
            return result

        source.get_quote = then_an_order  # type: ignore[method-assign]
        await recorder.poll()

        assert len(source.calls) == 1 and len(sink.rows) == 50
        assert recorder.counters.orders_yielded == 1


class TestBackOff:
    async def test_a_rate_limit_reply_pauses_the_recorder_and_it_doubles(self) -> None:
        recorder, source, sink, clock, _ = rig(IDS)
        source.raises = [BrokerRateLimitedError("403 rate limit"), BrokerRateLimitedError("again")]

        await recorder.poll()  # limited: back off 60 s
        assert recorder.counters.rate_limited == 1 and source.calls == [IDS]
        clock.advance(timedelta(seconds=30))
        await recorder.poll()
        assert len(source.calls) == 1 and recorder.counters.backed_off == 1

        clock.advance(timedelta(seconds=31))
        await recorder.poll()  # limited again: back off 120 s
        assert len(source.calls) == 2 and recorder.counters.rate_limited == 2
        clock.advance(timedelta(seconds=119))
        await recorder.poll()
        assert len(source.calls) == 2

        clock.advance(timedelta(seconds=2))
        await recorder.poll()  # served
        assert len(source.calls) == 3 and len(sink.rows) == 3

    async def test_the_backoff_is_capped_and_a_success_resets_it(self) -> None:
        settings = RecorderSettings(
            backoff_initial=timedelta(seconds=60), backoff_max=timedelta(seconds=100)
        )
        recorder, source, _, clock, _ = rig(IDS, settings=settings)
        source.raises = [BrokerRateLimitedError("x")] * 3

        for _ in range(3):
            await recorder.poll()
            clock.advance(timedelta(seconds=101))  # past any capped wait
        assert recorder.counters.rate_limited == 3
        await recorder.poll()  # success
        source.raises = [BrokerRateLimitedError("x")]
        clock.advance(timedelta(seconds=1))
        await recorder.poll()
        clock.advance(timedelta(seconds=61))  # back to 60 s, not 240
        await recorder.poll()
        assert recorder.counters.rate_limited == 4 and len(source.calls) == 6

    async def test_a_rate_limit_keeps_the_rows_of_batches_already_served(self) -> None:
        ids = [f"NSE:{n}" for n in range(120)]
        recorder, source, sink, _, _ = rig(ids)
        source.raises = [None, BrokerRateLimitedError("x")]

        await recorder.poll()

        assert len(source.calls) == 2 and len(sink.rows) == 50


class TestFailuresNeverRaise:
    async def test_a_broker_error_is_counted_and_the_next_batch_is_tried(self) -> None:
        ids = [f"NSE:{n}" for n in range(60)]
        recorder, source, sink, _, _ = rig(ids)
        source.raises = [BrokerTransportError("reset")]

        await recorder.poll()

        assert len(source.calls) == 2 and len(sink.rows) == 10
        assert recorder.counters.errors == 1
        assert recorder.counters.by_error == {"BrokerTransportError": 1}

    async def test_a_slow_request_times_out_and_is_counted(self) -> None:
        settings = RecorderSettings(request_timeout=timedelta(milliseconds=10))
        recorder, source, sink, _, _ = rig(IDS, settings=settings)
        source.delay = 1.0

        await recorder.poll()

        assert recorder.counters.timeouts == 1 and sink.rows == []

    async def test_a_poll_stops_when_its_budget_is_spent(self) -> None:
        ids = [f"NSE:{n}" for n in range(150)]
        settings = RecorderSettings(budget=timedelta(seconds=10))
        recorder, source, sink, _, _ = rig(ids, settings=settings)
        source.tick = timedelta(seconds=6)

        await recorder.poll()

        assert len(source.calls) == 2 and recorder.counters.over_budget == 1
        assert len(sink.rows) == 100


class TestSettingsAndConstruction:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"batch_size": 0},
            {"batch_size": MAX_BATCH + 1},
            {"interval": timedelta(0)},
            {"request_timeout": timedelta(0)},
            {"backoff_initial": timedelta(0)},
            {"backoff_initial": timedelta(seconds=60), "backoff_max": timedelta(seconds=30)},
            {"flush_every_polls": 0},
        ],
    )
    def test_nonsense_settings_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            RecorderSettings(**kwargs)  # type: ignore[arg-type]

    def test_a_recorder_with_nothing_to_record_is_refused(self) -> None:
        clock = FixedClock(OPEN)
        with pytest.raises(ValueError, match="nothing to record"):
            QuoteRecorder(Source(clock), Sink(), [], RecorderSettings(), clock)

    def test_it_defaults_to_the_session_window_and_no_order_priority(self) -> None:
        clock = FixedClock(OPEN)
        recorder = QuoteRecorder(Source(clock), Sink(), IDS, RecorderSettings(), clock)

        assert asyncio.run(recorder.poll()) is None
        assert recorder.counters.polls == 1
