"""EM-246: near-the-money option quotes: the scrip master's rows, the strike window, the recorder's
15-minute rebuild, degrading on a rate limit, and the option files."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from emporos.broker.errors import BrokerRateLimitedError
from emporos.broker.models import Quote
from emporos.core.clock import IST, FixedClock
from emporos.domain.money import Money
from emporos.quotes.contract import ContractBook, OptionContract
from emporos.quotes.option_recorder import OptionQuoteRecorder, OptionSettings
from emporos.quotes.option_row import ParquetOptionSink
from emporos.quotes.strikes import StrikePlanner, UnderlyingRule

OPEN = datetime(2026, 9, 25, 10, 0, tzinfo=IST)  # a Friday inside the session
NIFTY = UnderlyingRule("NIFTY", "NSE:99926000", expiries=2, each_side=2)
STOCK = UnderlyingRule("TCS", "NSE:11536", expiries=1, each_side=1)


def master_row(name: str, token: int, expiry: str, strike: int, right: str) -> dict[str, str]:
    return {
        "token": str(token), "symbol": f"{name}{expiry}{strike}{right}", "name": name,
        "expiry": expiry, "strike": f"{strike * 100}.000000", "lotsize": "75",
        "instrumenttype": "OPTIDX" if name == "NIFTY" else "OPTSTK", "exch_seg": "NFO",
    }  # fmt: skip


def book() -> ContractBook:
    rows = []
    token = 1000
    for expiry in ("29SEP2026", "06OCT2026", "13OCT2026"):
        for strike in range(24000, 24600, 100):
            for right in ("CE", "PE"):
                token += 1
                rows.append(master_row("NIFTY", token, expiry, strike, right))
    for strike in (3000, 3100, 3200):
        for right in ("CE", "PE"):
            token += 1
            rows.append(master_row("TCS", token, "29SEP2026", strike, right))
    rows.append({**rows[0], "exch_seg": "NSE"})  # not a derivative segment
    rows.append({**rows[0], "expiry": "garbage"})  # a row that does not parse
    return ContractBook.from_master_rows(rows)


class TestContracts:
    def test_a_master_row_becomes_a_contract_with_rupee_strike_and_a_date(self) -> None:
        c = OptionContract.from_master_row(master_row("NIFTY", 7, "29SEP2026", 24500, "CE"))

        assert c == OptionContract("NFO:7", "NIFTY", date(2026, 9, 29), Decimal("24500"), "CE", 75)

    def test_unparsable_and_non_option_rows_are_skipped(self) -> None:
        assert len(book()) == 3 * 6 * 2 + 6  # the two bad rows are not in it

    def test_expiries_come_sorted_from_today(self) -> None:
        b = book()

        assert b.expiries("NIFTY", date(2026, 9, 30)) == [date(2026, 10, 6), date(2026, 10, 13)]
        assert b.expiries("NOPE", date(2026, 9, 1)) == []


class TestStrikeWindow:
    def test_it_centres_on_the_listed_strike_nearest_the_spot(self) -> None:
        chosen = StrikePlanner(book()).plan(NIFTY, Decimal("24290"), date(2026, 9, 25))

        assert {c.strike for c in chosen} == {
            Decimal(s) for s in (24100, 24200, 24300, 24400, 24500)
        }
        assert {c.expiry for c in chosen} == {date(2026, 9, 29), date(2026, 10, 6)}
        assert len(chosen) == 5 * 2 * 2  # strikes x calls and puts x two expiries

    def test_the_window_is_cut_at_the_edge_of_the_listed_strikes(self) -> None:
        chosen = StrikePlanner(book()).plan(STOCK, Decimal("2000"), date(2026, 9, 25))

        assert {c.strike for c in chosen} == {Decimal(3000), Decimal(3100)}

    def test_a_rule_needs_an_expiry(self) -> None:
        with pytest.raises(ValueError):
            UnderlyingRule("X", "NSE:1", expiries=0, each_side=1)


def quote(instrument: str, at: datetime, ltp: str = "100", oi: int | None = 500) -> Quote:
    return Quote(
        instrument, Money.of(ltp), Money.of("99"), Money.of("101"), Money.of("98"),
        Money.of("99.5"), 10, at.astimezone(UTC), bid=Money.of("99.9"), ask=Money.of("100.1"),
        bid_qty=5, ask_qty=6, open_interest=oi,
    )  # fmt: skip


class Source:
    """Spots by id; every other id answers a fresh quote; the script can raise."""

    def __init__(self, clock: FixedClock) -> None:
        self.clock, self.calls = clock, []
        self.spots = {"NSE:99926000": "24290", "NSE:11536": "3100"}
        self.raises: list[BaseException | None] = []

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        self.calls.append(list(instrument_ids))
        if self.raises and (error := self.raises.pop(0)) is not None:
            raise error
        now = self.clock.now()
        return [quote(i, now, self.spots.get(i, "100")) for i in instrument_ids]


class Master:
    def __init__(self, fail: int = 0) -> None:
        self.fail, self.loads = fail, 0

    async def load(self) -> ContractBook:
        self.loads += 1
        if self.fail:
            self.fail -= 1
            raise OSError("no network")
        return book()


class Sink:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.flushes = 0

    def append(self, rows: Sequence[object]) -> None:
        self.rows.extend(rows)

    def flush(self) -> int:
        self.flushes += 1
        return 0


def recorder(
    clock: FixedClock, source: Source, master: Master | None = None, **kw: object
) -> tuple[OptionQuoteRecorder, Sink]:
    sink = Sink()
    rec = OptionQuoteRecorder(
        source, sink, master or Master(), [NIFTY, STOCK],  # type: ignore[arg-type]
        OptionSettings(**kw), clock,  # type: ignore[arg-type]
    )  # fmt: skip
    return rec, sink


class TestRecorder:
    async def test_it_records_the_window_with_contract_spot_and_open_interest(self) -> None:
        clock = FixedClock(OPEN)
        rec, sink = recorder(clock, Source(clock))

        await rec.poll()

        assert len(sink.rows) == rec.counters.contracts == 5 * 2 * 2 + 3 * 2
        row = sink.rows[0]
        assert row.contract.underlying in {"NIFTY", "TCS"} and row.open_interest == 500  # type: ignore[attr-defined]
        assert {r.spot for r in sink.rows if r.contract.underlying == "NIFTY"} == {Decimal("24290")}  # type: ignore[attr-defined]

    async def test_the_set_follows_the_spot_every_fifteen_minutes_and_not_before(self) -> None:
        clock = FixedClock(OPEN)
        source = Source(clock)
        rec, _ = recorder(clock, source)
        await rec.poll()
        first = set(rec.instrument_ids)

        source.spots["NSE:99926000"] = "24000"
        clock.advance(timedelta(minutes=14))
        await rec.poll()
        assert set(rec.instrument_ids) == first and rec.counters.rebuilds == 1

        clock.advance(timedelta(minutes=2))
        await rec.poll()
        assert set(rec.instrument_ids) != first and rec.counters.rebuilds == 2

    async def test_a_failed_scrip_master_is_retried_later_and_never_raises(self) -> None:
        clock = FixedClock(OPEN)
        master = Master(fail=1)
        rec, sink = recorder(clock, Source(clock), master)

        await rec.poll()
        assert sink.rows == [] and rec.counters.master_failures == 1

        clock.advance(timedelta(minutes=5))
        await rec.poll()
        assert master.loads == 1  # not yet: the retry waits ten minutes

        clock.advance(timedelta(minutes=6))
        await rec.poll()
        assert master.loads == 2 and len(sink.rows) > 0

    async def test_a_rate_limit_makes_it_poll_less_often_and_it_recovers(self) -> None:
        clock = FixedClock(OPEN)
        source = Source(clock)
        rec, _ = recorder(clock, source, recover_after=2, interval=timedelta(seconds=60))
        await rec.poll()  # the spot call, then the quotes: clean
        source.raises = [None, BrokerRateLimitedError("slow down")]
        clock.advance(timedelta(minutes=16))  # a rebuild: the spot call is the clean one
        await rec.poll()

        assert rec.interval == timedelta(seconds=120) and rec.counters.degraded == 1

        clock.advance(timedelta(minutes=16))  # past the back-off and the rebuild
        await rec.poll()
        clock.advance(timedelta(minutes=3))
        await rec.poll()
        assert rec.interval == timedelta(seconds=60)

    async def test_an_answer_for_an_unknown_id_is_counted_not_recorded(self) -> None:
        clock = FixedClock(OPEN)

        class Extra(Source):
            async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
                return [*await super().get_quote(instrument_ids), quote("NFO:999999", clock.now())]

        rec, sink = recorder(clock, Extra(clock))
        await rec.poll()

        assert rec.counters.unknown_quotes == 1 and all(
            r.contract.instrument_id != "NFO:999999"
            for r in sink.rows  # type: ignore[attr-defined]
        )

    async def test_outside_the_session_it_calls_nothing_and_flushes(self) -> None:
        clock = FixedClock(datetime(2026, 9, 25, 16, 0, tzinfo=IST))
        source = Source(clock)
        rec, sink = recorder(clock, source)

        await rec.poll()

        assert source.calls == [] and sink.flushes == 1

    def test_nonsense_is_refused(self) -> None:
        clock = FixedClock(OPEN)
        with pytest.raises(ValueError):
            OptionQuoteRecorder(Source(clock), Sink(), Master(), [], OptionSettings(), clock)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            OptionSettings(interval=timedelta(minutes=10), max_interval=timedelta(minutes=1))


class TestSink:
    async def test_rows_land_in_their_own_part_files_with_the_contract_columns(
        self, tmp_path: Path
    ) -> None:
        clock = FixedClock(OPEN)
        sink = ParquetOptionSink(tmp_path, clock)
        rec = OptionQuoteRecorder(Source(clock), sink, Master(), [STOCK], OptionSettings(), clock)

        await rec.poll()
        rec.close()

        [path] = tmp_path.glob("date=2026-09-25/part-*.parquet")
        table = pq.read_table(path)
        assert table.num_rows == 3 * 2
        assert {"underlying", "expiry", "strike", "right", "spot", "open_interest"} <= set(
            table.column_names
        )
        assert table.column("underlying").to_pylist() == ["TCS"] * 6
