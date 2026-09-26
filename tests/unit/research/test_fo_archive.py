"""EM-225: the F&O bhavcopy readers, layouts, store, ledger and the polite fetcher, pinned."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from emporos.core.clock import FixedClock
from emporos.options.chain import OptionRight
from emporos.research.fo_archive_fetch import (
    USER_AGENT,
    ArchiveFetcher,
    ArchiveHalted,
    ArchiveRefused,
    weekdays_descending,
)
from emporos.research.fo_archive_layout import CutoverLayout, legacy_url, udiff_url
from emporos.research.fo_archive_rows import (
    ArchiveFormat,
    ArchiveParseError,
    InstrumentKind,
    read_archive,
)
from emporos.research.fo_archive_store import FetchOutcome, FoDayStore, FoLedger

UDIFF_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
    "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
    "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
)
DAY = date(2024, 9, 30)
UDIFF_CSV = UDIFF_HEADER + (
    "2024-09-30,2024-09-30,FO,NSE,IDO,54773,,NIFTY,,2024-10-31,2024-10-31,24950.00,PE,"
    "NIFTY24OCT24950PE,67.55,100.05,65.70,89.55,91.15,59.70,25810.85,89.55,24400,-1175,4171,"
    "2610518345.00,1122,F1,25,,,,,\n"
    "2024-09-30,2024-09-30,FO,NSE,IDO,62968,,NIFTY,,2024-10-31,2024-10-31,27300.00,PE,"
    "NIFTY24OCT27300PE,0.00,0.00,0.00,980.55,988.75,980.55,25810.85,1416.40,2700,0,0,0.00,0,F1,"
    "25,,,,,\n"
    "2024-09-30,2024-09-30,FO,NSE,IDF,35089,,NIFTY,,2024-11-28,2024-11-28,,,NIFTY24NOVFUT,"
    "26399.00,26401.40,26086.00,26123.80,26115.00,26459.15,25810.85,26123.80,1102750,-75900,"
    "26876,17610263267.50,16174,F1,25,,,,,\n"
    "2024-09-30,2024-09-30,FO,NSE,STF,35304,,BANKBARODA,,2024-12-26,2024-12-26,,,BANKBARODA24DECFUT,"
    "254.00,256.55,251.90,252.35,251.90,254.35,247.80,252.35,447525,386100,162,120148031.25,82,F1,"
    "2925,,,,,\n"
)
LEGACY_DAY = date(2015, 3, 2)
LEGACY_HEADER = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
    "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
)
LEGACY_CSV = LEGACY_HEADER + (
    "FUTIDX,BANKNIFTY,26-Mar-2015,0,XX,20150.15,20245,19883,20174.25,20174.25,186110,934308.79,"
    "1995450,-28300,02-MAR-2015,\n"
    "OPTIDX,BANKNIFTY,26-Mar-2015,16700,CE,3318.6,3318.6,3318.6,3318.6,3318.6,1,5,19600,25,"
    "02-MAR-2015,\n"
    "OPTSTK,ACC,26-Mar-2015,1400,CE,1,1,1,1,1,1,1,1,1,02-MAR-2015,\n"
)


def zipped(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


class TestReaders:
    def test_udiff_keeps_index_contracts_only_with_lot_size_and_underlying(self) -> None:
        rows = read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)

        assert [(r.symbol, r.kind) for r in rows] == [
            ("NIFTY", InstrumentKind.OPTION),
            ("NIFTY", InstrumentKind.OPTION),
            ("NIFTY", InstrumentKind.FUTURE),
        ]
        option = rows[0]
        assert (option.strike, option.right, option.expiry) == (
            Decimal("24950.00"), OptionRight.PUT, date(2024, 10, 31)
        )  # fmt: skip
        assert (option.close, option.settle, option.contracts) == (
            Decimal("89.55"), Decimal("89.55"), 4171
        )  # fmt: skip
        assert (option.open_interest, option.change_in_oi) == (24400, -1175)
        assert (option.lot_size, option.underlying) == (25, Decimal("25810.85"))
        assert rows[2].strike is None and rows[2].right is None

    def test_a_contract_that_did_not_trade_keeps_its_carried_close_but_zero_contracts(self) -> None:
        stale = read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)[1]

        assert (stale.contracts, stale.close, stale.settle) == (
            0,
            Decimal("980.55"),
            Decimal("1416.40"),
        )

    def test_legacy_reads_index_rows_and_leaves_lot_size_and_underlying_unknown(self) -> None:
        rows = read_archive(LEGACY_DAY, zipped("f.csv", LEGACY_CSV), ArchiveFormat.LEGACY)

        assert [(r.symbol, r.kind) for r in rows] == [
            ("BANKNIFTY", InstrumentKind.FUTURE),
            ("BANKNIFTY", InstrumentKind.OPTION),
        ]
        future = rows[0]
        assert (future.lot_size, future.underlying) == (None, None)
        assert future.expiry == date(2015, 3, 26)
        assert future.turnover == Decimal("934308.79") * 100_000  # lakhs to rupees
        assert rows[1].right is OptionRight.CALL and rows[1].strike == Decimal(16700)

    def test_the_oldest_legacy_files_name_the_option_type_column_differently(self) -> None:
        old = LEGACY_CSV.replace("OPTION_TYP", "OPTIONTYPE").replace("02-MAR-2015", "2-MAR-2015")

        rows = read_archive(LEGACY_DAY, zipped("f.csv", old), ArchiveFormat.LEGACY)

        assert rows[1].right is OptionRight.CALL and rows[0].day == LEGACY_DAY

    def test_a_file_for_another_day_is_refused(self) -> None:
        with pytest.raises(ArchiveParseError, match="in the file of"):
            read_archive(date(2024, 9, 27), zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)

    def test_missing_columns_or_a_bad_zip_are_refused(self) -> None:
        with pytest.raises(ArchiveParseError, match="columns missing"):
            read_archive(DAY, zipped("x.csv", "a,b\n1,2\n"), ArchiveFormat.UDIFF)
        with pytest.raises(ArchiveParseError, match="not a zip"):
            read_archive(DAY, b"<html>blocked</html>", ArchiveFormat.UDIFF)
        with pytest.raises(ArchiveParseError, match="one CSV"):
            read_archive(DAY, zipped("x.txt", "hi"), ArchiveFormat.UDIFF)

    def test_a_fractional_count_is_refused_not_rounded(self) -> None:
        bad = UDIFF_CSV.replace(",4171,", ",4171.5,", 1)

        with pytest.raises(ArchiveParseError, match="not a whole number"):
            read_archive(DAY, zipped("x.csv", bad), ArchiveFormat.UDIFF)


class TestLayout:
    def test_the_two_urls_are_the_exchanges_own_paths(self) -> None:
        assert udiff_url(DAY) == (
            "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20240930_F_0000.csv.zip"
        )
        assert legacy_url(LEGACY_DAY) == (
            "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/2015/MAR/"
            "fo02MAR2015bhav.csv.zip"
        )

    def test_legacy_before_the_cutover_udiff_after_and_both_in_the_overlap(self) -> None:
        layout = CutoverLayout(date(2024, 7, 8), 7)

        assert [c.format for c in layout.candidates(date(2024, 1, 2))] == [ArchiveFormat.LEGACY]
        assert [c.format for c in layout.candidates(date(2024, 7, 3))] == [
            ArchiveFormat.LEGACY,
            ArchiveFormat.UDIFF,
        ]
        assert [c.format for c in layout.candidates(date(2024, 7, 8))] == [ArchiveFormat.UDIFF]


class TestStore:
    def test_a_day_round_trips_exactly(self, tmp_path: Path) -> None:
        rows = read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)
        store = FoDayStore(tmp_path)

        assert store.write(DAY, rows) == 3
        again = store.read(DAY)

        assert sorted(again, key=lambda r: (r.kind.value, r.strike or 0)) == sorted(
            rows, key=lambda r: (r.kind.value, r.strike or 0)
        )
        assert store.days() == [DAY] and store.has(DAY) and not store.has(date(2024, 10, 1))

    def test_legacy_rows_keep_their_unknowns_as_none(self, tmp_path: Path) -> None:
        rows = read_archive(LEGACY_DAY, zipped("f.csv", LEGACY_CSV), ArchiveFormat.LEGACY)
        store = FoDayStore(tmp_path)
        store.write(LEGACY_DAY, rows)

        assert {(r.lot_size, r.underlying) for r in store.read(LEGACY_DAY)} == {(None, None)}

    def test_a_row_of_another_day_is_refused(self, tmp_path: Path) -> None:
        rows = read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)

        with pytest.raises(ValueError, match="different day"):
            FoDayStore(tmp_path).write(date(2024, 10, 1), rows)

    def test_no_temp_file_is_left_behind(self, tmp_path: Path) -> None:
        FoDayStore(tmp_path).write(
            DAY, read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)
        )

        assert list(tmp_path.rglob("*.tmp")) == []


class RecordingSleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def fetcher(
    tmp_path: Path, handler: httpx.MockTransport, sleeper: RecordingSleeper | None = None,
    layout: CutoverLayout | None = None,
) -> tuple[ArchiveFetcher, FoLedger, FoDayStore, RecordingSleeper]:  # fmt: skip
    sleeper = sleeper or RecordingSleeper()
    ledger, store = FoLedger(tmp_path / "ledger.jsonl"), FoDayStore(tmp_path / "fo")
    client = httpx.AsyncClient(transport=handler)
    clock = FixedClock(datetime(2026, 9, 25, tzinfo=UTC))
    return (
        ArchiveFetcher(client, layout or CutoverLayout(), store, ledger, clock, sleeper),
        ledger,
        store,
        sleeper,
    )


THURSDAY = date(2024, 9, 26)


def requested_day(request: httpx.Request) -> date:
    stamp = str(request.url).rsplit("_", 3)[-3]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def udiff_payload(day: date) -> bytes:
    text = UDIFF_CSV.replace("2024-09-30", day.isoformat())
    return zipped("f.csv", text)


class TestFetcher:
    async def test_it_fetches_newest_first_records_provenance_and_sleeps_between_requests(
        self, tmp_path: Path
    ) -> None:
        asked: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            assert request.headers["user-agent"] == USER_AGENT
            return httpx.Response(200, content=udiff_payload(requested_day(request)))

        f, ledger, store, sleeper = fetcher(tmp_path, httpx.MockTransport(handle))

        report = await f.run(date(2024, 9, 25), date(2024, 9, 26))

        assert asked == [udiff_url(date(2024, 9, 26)), udiff_url(date(2024, 9, 25))]
        assert sleeper.slept == [3.0]  # none before the first request, then one gap
        assert (report.fetched, report.rows) == (2, 6)
        entries = ledger.load()
        assert [e.day for e in entries] == [date(2024, 9, 26), date(2024, 9, 25)]
        assert entries[0].url == udiff_url(date(2024, 9, 26)) and len(entries[0].sha256) == 64
        assert (
            entries[0].outcome is FetchOutcome.FETCHED and entries[0].source is ArchiveFormat.UDIFF
        )
        assert store.days() == [date(2024, 9, 25), date(2024, 9, 26)]

    async def test_a_404_is_a_recorded_absent_day_not_an_error(self, tmp_path: Path) -> None:
        f, ledger, _, _ = fetcher(tmp_path, httpx.MockTransport(lambda r: httpx.Response(404)))

        report = await f.run(THURSDAY, THURSDAY)

        assert (report.absent, report.fetched) == (1, 0)
        (entry,) = ledger.load()
        assert entry.outcome is FetchOutcome.ABSENT and entry.rows == 0

    async def test_in_the_overlap_it_falls_back_to_the_second_layout(self, tmp_path: Path) -> None:
        day = date(2024, 7, 3)
        asked: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            if "historical" in str(request.url):
                return httpx.Response(404)
            return httpx.Response(200, content=udiff_payload(day))

        f, ledger, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))

        await f.run(day, day)

        assert asked == [legacy_url(day), udiff_url(day)]
        assert ledger.load()[0].source is ArchiveFormat.UDIFF

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_a_refusal_stops_the_run_at_once_and_records_nothing(
        self, tmp_path: Path, status: int
    ) -> None:
        calls: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(status)

        f, ledger, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))

        with pytest.raises(ArchiveRefused, match=str(status)):
            await f.run(date(2024, 9, 20), date(2024, 9, 26))

        assert len(calls) == 1  # no retry, no next date
        assert ledger.load() == []

    async def test_a_resumed_run_skips_dates_already_in_the_ledger(self, tmp_path: Path) -> None:
        calls = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=udiff_payload(THURSDAY))

        f, _, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))
        await f.run(THURSDAY, THURSDAY)
        again, _, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))

        report = await again.run(THURSDAY, THURSDAY)

        assert calls == 1 and report.skipped == 1

    async def test_a_ledgered_day_whose_file_was_wiped_is_fetched_again(
        self, tmp_path: Path
    ) -> None:
        calls = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=udiff_payload(THURSDAY))

        f, _, store, _ = fetcher(tmp_path, httpx.MockTransport(handle))
        await f.run(THURSDAY, THURSDAY)
        store.path(THURSDAY).unlink()  # the data directory is emptied, the ledger stays
        again, _, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))

        report = await again.run(THURSDAY, THURSDAY)

        assert calls == 2 and report.skipped == 0 and store.has(THURSDAY)

    async def test_three_failures_in_a_row_halt_the_run_and_leave_the_dates_unrecorded(
        self, tmp_path: Path
    ) -> None:
        f, ledger, _, _ = fetcher(tmp_path, httpx.MockTransport(lambda r: httpx.Response(503)))

        with pytest.raises(ArchiveHalted):
            await f.run(date(2024, 9, 16), date(2024, 9, 26))

        assert ledger.load() == []

    async def test_a_success_resets_the_failure_count(self, tmp_path: Path) -> None:
        answers = iter([503, 200, 503, 503, 200])

        def handle(request: httpx.Request) -> httpx.Response:
            if next(answers) == 200:
                return httpx.Response(200, content=udiff_payload(requested_day(request)))
            return httpx.Response(503)

        f, ledger, _, _ = fetcher(tmp_path, httpx.MockTransport(handle))

        report = await f.run(date(2024, 9, 20), date(2024, 9, 26))  # 26,25,24,23,20

        assert (report.fetched, len(report.failed)) == (2, 3)  # never 3 in a row
        assert [e.day for e in ledger.load()] == [date(2024, 9, 25), date(2024, 9, 20)]

    async def test_a_download_that_is_not_a_zip_is_a_failure_not_a_stored_day(
        self, tmp_path: Path
    ) -> None:
        f, ledger, store, _ = fetcher(
            tmp_path, httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>"))
        )

        with pytest.raises(ArchiveHalted):
            await f.run(date(2024, 9, 24), date(2024, 9, 26))

        assert ledger.load() == [] and store.days() == []

    def test_requests_must_be_at_least_a_second_apart(self, tmp_path: Path) -> None:
        client = httpx.AsyncClient()
        with pytest.raises(ValueError, match="second apart"):
            ArchiveFetcher(
                client, CutoverLayout(), FoDayStore(tmp_path), FoLedger(tmp_path / "l"),
                FixedClock(datetime(2026, 9, 25, tzinfo=UTC)), RecordingSleeper(), 0.5,
            )  # fmt: skip


def test_weekdays_descending_skips_weekends() -> None:
    days = list(weekdays_descending(date(2024, 9, 20), date(2024, 9, 24)))

    assert days == [date(2024, 9, 24), date(2024, 9, 23), date(2024, 9, 20)]
