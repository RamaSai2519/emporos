"""EM-217: the Parquet quote store, the recording window and the row type."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.broker.models import Quote
from emporos.core.clock import IST, FixedClock
from emporos.domain.money import Money
from emporos.quotes.row import QuoteRow
from emporos.quotes.sink import SCHEMA, ParquetQuoteSink, read_day
from emporos.quotes.window import RecordingWindow

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=IST)


def row(instrument: str = "NSE:1", at: datetime = T0, **kw: object) -> QuoteRow:
    base: dict[str, object] = dict(
        instrument_id=instrument, received_at=at.astimezone(UTC), exchange_ts=at.astimezone(UTC),
        ltp=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10"), bid_qty=10,
        ask_qty=20, volume=1000,
    )  # fmt: skip
    return QuoteRow(**{**base, **kw})  # type: ignore[arg-type]


class TestParquetSink:
    def test_rows_round_trip_with_paise_exact_prices(self, tmp_path: Path) -> None:
        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))
        sink.append([row("NSE:1"), row("NSE:2", bid=None, bid_qty=None)])

        assert sink.flush() == 2

        table = read_day(tmp_path, date(2026, 9, 25))
        assert table.schema.equals(SCHEMA) and table.num_rows == 2
        first = table.to_pylist()[0]
        assert first["ltp"] == Decimal("100.05") and first["bid_qty"] == 10
        assert table.to_pylist()[1]["bid"] is None and table.to_pylist()[1]["bid_qty"] is None
        assert first["received_at"] == T0.astimezone(UTC)

    def test_every_flush_is_a_new_file_and_nothing_is_rewritten(self, tmp_path: Path) -> None:
        clock = FixedClock(T0)
        sink = ParquetQuoteSink(tmp_path, clock)
        sink.append([row()])
        sink.flush()
        first = sorted((tmp_path / "date=2026-09-25").glob("*.parquet"))
        first_bytes = first[0].read_bytes()

        clock.advance(timedelta(minutes=1))
        sink.append([row("NSE:2", T0 + timedelta(minutes=1))])
        sink.flush()

        files = sorted((tmp_path / "date=2026-09-25").glob("*.parquet"))
        assert len(files) == 2 and files[0].read_bytes() == first_bytes
        assert read_day(tmp_path, date(2026, 9, 25)).num_rows == 2

    def test_a_flush_of_nothing_writes_nothing(self, tmp_path: Path) -> None:
        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))

        assert sink.flush() == 0
        assert list(tmp_path.iterdir()) == []

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))
        sink.append([row()])
        sink.flush()

        assert not list(tmp_path.rglob("*.tmp"))

    def test_rows_are_filed_under_their_ist_day(self, tmp_path: Path) -> None:
        late = datetime(2026, 9, 25, 23, 50, tzinfo=IST)  # 18:20 UTC on the 25th
        after_midnight_ist = datetime(2026, 9, 26, 0, 10, tzinfo=IST)  # 18:40 UTC: still the 25th
        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))
        sink.append([row(at=late), row(at=after_midnight_ist)])

        sink.flush()

        assert read_day(tmp_path, date(2026, 9, 25)).num_rows == 1
        assert read_day(tmp_path, date(2026, 9, 26)).num_rows == 1

    def test_a_day_with_no_files_reads_as_an_empty_table(self, tmp_path: Path) -> None:
        table = read_day(tmp_path, date(2026, 1, 1))

        assert table.num_rows == 0 and table.schema.equals(SCHEMA)


class TestRecordingWindow:
    @pytest.mark.parametrize(
        ("moment", "inside"),
        [
            (datetime(2026, 9, 25, 9, 14, 59, tzinfo=IST), False),
            (datetime(2026, 9, 25, 9, 15, tzinfo=IST), True),
            (datetime(2026, 9, 25, 15, 29, 59, tzinfo=IST), True),
            (datetime(2026, 9, 25, 15, 30, tzinfo=IST), False),
            (datetime(2026, 9, 26, 10, 0, tzinfo=IST), False),  # Saturday
            (datetime(2026, 9, 27, 10, 0, tzinfo=IST), False),  # Sunday
            (datetime(2026, 9, 25, 4, 45, tzinfo=UTC), True),  # 10:15 IST given in UTC
        ],
    )
    def test_the_cash_session_on_weekdays(self, moment: datetime, inside: bool) -> None:
        assert RecordingWindow().contains(moment) is inside

    def test_a_window_must_open_before_it_closes(self) -> None:
        from datetime import time

        with pytest.raises(ValueError):
            RecordingWindow(time(15, 30), time(9, 15))


class TestQuoteRow:
    def test_it_is_built_from_a_broker_quote(self) -> None:
        quote = Quote(
            "NSE:1", Money.of("100.05"), Money.of("99"), Money.of("101"), Money.of("98"),
            Money.of("99.5"), 1000, T0.astimezone(UTC), bid=Money.of("100"), ask=None,
            bid_qty=10,
        )  # fmt: skip

        built = QuoteRow.from_quote(quote, T0.astimezone(UTC))

        assert (built.ltp, built.bid, built.ask, built.bid_qty, built.ask_qty) == (
            Decimal("100.05"), Decimal("100"), None, 10, None,
        )  # fmt: skip
        assert built.volume == 1000

    def test_naive_times_are_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            row(received_at=datetime(2026, 9, 25, 10, 0))
        with pytest.raises(ValueError, match="timezone-aware"):
            row(exchange_ts=datetime(2026, 9, 25, 10, 0))


class TestDaySummary:
    def test_a_day_is_summarised(self, tmp_path: Path) -> None:
        from emporos.quotes.summary import summarize_day

        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))
        second = T0 + timedelta(minutes=1)
        sink.append(
            [
                row("NSE:1"),  # spread 0.10 on 100.05: about 9.995 bps
                row("NSE:2", bid=None, bid_qty=None),
                row("NSE:1", second),
                row("NSE:2", second, bid=Decimal("101"), ask=Decimal("100")),  # crossed: ignored
            ]
        )
        sink.flush()

        s = summarize_day(tmp_path, date(2026, 9, 25))

        assert (s.files, s.rows, s.instruments, s.polls) == (1, 4, 2, 2)
        assert s.two_sided_share == pytest.approx(0.75)
        assert s.median_spread_bps == pytest.approx(9.995, abs=0.01)
        assert s.first_received == T0.astimezone(UTC) and s.last_received == second.astimezone(UTC)
        assert "4 rows in 1 files, 2 instruments, 2 polls" in s.lines()[0]

    def test_an_empty_day_says_so(self, tmp_path: Path) -> None:
        from emporos.quotes.summary import summarize_day

        s = summarize_day(tmp_path, date(2026, 9, 25))

        assert s.rows == 0 and "nothing recorded" in s.lines()[0]

    def test_a_day_with_no_two_sided_rows_has_no_spread(self, tmp_path: Path) -> None:
        from emporos.quotes.summary import summarize_day

        sink = ParquetQuoteSink(tmp_path, FixedClock(T0))
        sink.append([row(bid=None, bid_qty=None)])
        sink.flush()

        s = summarize_day(tmp_path, date(2026, 9, 25))

        assert s.median_spread_bps is None and "n/a" in s.lines()[2]
