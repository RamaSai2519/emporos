"""EM-241: the single-stock F&O dataset: parsers, thinning, lot sizes, chains, the report."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tests.unit.research.test_fo_archive import LEGACY_CSV, UDIFF_CSV, zipped

from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import (
    ArchiveFormat,
    IndexContractRow,
    InstrumentKind,
    read_archive,
)
from emporos.research.fo_archive_store import FetchOutcome, FoDayStore, FoLedger, LedgerEntry
from emporos.research.fo_stock_archive import StockArchiveReader, StockRowFilter
from emporos.research.fo_stock_chains import StockChains, StockLotBook
from emporos.research.fo_stock_report import StockArchiveReporter

D = Decimal
DAY = date(2024, 3, 5)
NEAR, NEXT = date(2024, 3, 28), date(2024, 4, 25)


def row(
    kind: InstrumentKind = InstrumentKind.OPTION, symbol: str = "ABC", expiry: date = NEAR,
    strike: str | None = "100", right: OptionRight | None = OptionRight.CALL, close: str = "5",
    contracts: int = 50, turnover: str = "0", lot: int | None = 500, underlying: str | None = "100",
    day: date = DAY, source: ArchiveFormat = ArchiveFormat.UDIFF,
) -> IndexContractRow:  # fmt: skip
    future = kind is InstrumentKind.FUTURE
    return IndexContractRow(
        day, symbol, kind, expiry, None if future else D(strike or 0), None if future else right,
        D(close), D(close), D(close), D(close), D(close), contracts, D(turnover), 1000, 0,
        None if underlying is None else D(underlying), lot, source,
    )  # fmt: skip


def future(symbol: str = "ABC", expiry: date = NEAR, **kw: object) -> IndexContractRow:
    return row(InstrumentKind.FUTURE, symbol, expiry, None, None, **kw)  # type: ignore[arg-type]


class TestReaders:
    def test_the_stock_reader_keeps_stock_rows_and_the_index_reader_never_sees_them(self) -> None:
        payload = zipped("x.csv", UDIFF_CSV)

        stock = StockArchiveReader()(date(2024, 9, 30), payload, ArchiveFormat.UDIFF)
        index = read_archive(date(2024, 9, 30), payload, ArchiveFormat.UDIFF)

        assert [r.symbol for r in stock] == ["BANKBARODA"]
        assert stock[0].kind is InstrumentKind.FUTURE and stock[0].lot_size == 2925
        assert {r.symbol for r in index} == {"NIFTY"}  # stock rows never reach the index dataset

    def test_legacy_stock_rows_are_read_and_index_rows_are_not(self) -> None:
        legacy = (
            LEGACY_CSV
            + "FUTSTK,ACC,26-Mar-2015,0,XX,1400,1400,1400,1400,1400,10,5,100,0,02-MAR-2015,\n"
        )
        rows = StockArchiveReader()(date(2015, 3, 2), zipped("f.csv", legacy), ArchiveFormat.LEGACY)

        kinds = {(r.symbol, r.kind) for r in rows}
        assert kinds == {("ACC", InstrumentKind.OPTION), ("ACC", InstrumentKind.FUTURE)}


class TestRowFilter:
    def test_only_strikes_within_the_band_of_the_underlying_survive(self) -> None:
        rows = [row(strike=s) for s in ("80", "84", "85", "100", "115", "116", "120")]

        kept = StockRowFilter().apply([*rows, future()])

        assert sorted(r.strike for r in kept if r.strike) == [D(s) for s in (85, 100, 115)]

    def test_only_monthly_expiries_survive_when_futures_list_them(self) -> None:
        weekly = date(2024, 3, 14)
        rows = [future(), row(), row(expiry=weekly)]

        kept = StockRowFilter().apply(rows)

        assert {r.expiry for r in kept} == {NEAR}

    def test_futures_are_kept_whole(self) -> None:
        kept = StockRowFilter().apply([future(), future(expiry=NEXT)])

        assert len(kept) == 2

    def test_without_a_carried_underlying_the_nearest_futures_settle_is_the_level(self) -> None:
        rows = [future(close="200", underlying=None), row(strike="100", underlying=None),
                row(strike="200", underlying=None)]  # fmt: skip

        kept = StockRowFilter().apply(rows)

        assert [r.strike for r in kept if r.strike] == [D(200)]

    def test_no_level_at_all_keeps_the_futures_only(self) -> None:
        assert StockRowFilter().apply([row(underlying=None)]) == []

    def test_each_stock_is_thinned_by_its_own_level(self) -> None:
        rows = [row(symbol="AAA", strike="100"), row(symbol="BBB", strike="100", underlying="50")]

        kept = StockRowFilter().apply(rows)

        assert [r.symbol for r in kept] == ["AAA"]


class TestLotBook:
    def test_an_exchange_lot_size_is_used_as_it_is(self) -> None:
        book = StockLotBook({"ABC": [500]})

        assert book.resolve("ABC", [row(lot=750), future(lot=750)]) == {NEAR: 750}

    def test_a_legacy_estimate_snaps_to_a_lot_the_exchange_published_for_that_stock(self) -> None:
        # 21,439 lots x 250 x 3,012.1 is the turnover; the day's average price makes it 1.3% higher
        turnover = D(21439) * 250 * D("3012.1") * D("1.013")
        legacy = future(lot=None, contracts=21439, turnover=str(turnover), close="3012.1")

        assert StockLotBook({"ABC": [250, 500]}).resolve("ABC", [legacy]) == {NEAR: 250}

    def test_an_estimate_that_matches_no_published_lot_is_left_unresolved(self) -> None:
        turnover = D(1000) * 375 * D(100)
        legacy = future(lot=None, contracts=1000, turnover=str(turnover), close="100")

        assert StockLotBook({"ABC": [250, 500]}).resolve("ABC", [legacy]) == {}

    def test_a_thinly_traded_future_gives_no_estimate(self) -> None:
        legacy = future(lot=None, contracts=3, turnover="150000", close="100")

        assert StockLotBook({"ABC": [500]}).resolve("ABC", [legacy]) == {}

    def test_the_book_reads_published_lots_from_the_store(self, tmp_path: Path) -> None:
        store = FoDayStore(tmp_path)
        store.write(DAY, [future(lot=500), future(symbol="XYZ", lot=None)])
        store.write(date(2024, 3, 6), [future(lot=550, day=date(2024, 3, 6))])

        book = StockLotBook.from_store(store)

        assert book.published("ABC") == (500, 550) and book.published("XYZ") == ()


class TestChains:
    def store(self, tmp_path: Path, rows: list[IndexContractRow]) -> FoDayStore:
        store = FoDayStore(tmp_path)
        store.write(DAY, rows)
        return store

    def test_a_days_rows_become_a_chain_with_lot_size_and_step(self, tmp_path: Path) -> None:
        rows = [future(), row(strike="100", close="6"), row(strike="110", close="2"),
                row(strike="100", right=OptionRight.PUT, close="4"),
                row(strike="100", expiry=NEXT, close="9")]  # fmt: skip
        store = self.store(tmp_path, rows)

        snap = StockChains(store, StockLotBook({})).snapshot("ABC", DAY)

        assert snap is not None
        assert snap.underlying_close == D(100) and snap.lot_size == 500
        assert snap.strike_step == D(10) and snap.expiry_dates == (NEAR, NEXT)
        quote = snap.expiries[NEAR].quote(D(100), OptionRight.CALL)
        assert quote is not None and quote.close == D(6) and quote.tradable

    def test_a_day_without_a_file_or_a_stock_without_rows_has_no_chain(
        self, tmp_path: Path
    ) -> None:
        chains = StockChains(self.store(tmp_path, [future(), row()]), StockLotBook({}))

        assert chains.snapshot("ABC", date(2024, 3, 6)) is None  # no file
        assert chains.snapshot("OTHER", DAY) is None

    def test_a_legacy_day_with_no_resolvable_lot_size_has_no_chain(self, tmp_path: Path) -> None:
        rows = [future(lot=None, underlying=None), row(lot=None, underlying=None)]
        chains = StockChains(self.store(tmp_path, rows), StockLotBook({}))

        assert chains.snapshot("ABC", DAY) is None


class TestReport:
    def test_files_rows_holidays_gaps_and_lot_changes(self, tmp_path: Path) -> None:
        store = FoDayStore(tmp_path / "s")
        d1, d2, d3 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)
        store.write(d1, [future(day=d1, lot=500)])
        store.write(d2, [future(day=d2, lot=500), future(symbol="OLD", day=d2, lot=None)])
        store.write(d3, [future(day=d3, lot=700), future(symbol="OLD", day=d3, lot=None)])
        ledger = FoLedger(tmp_path / "ledger.jsonl")
        ledger.record(
            LedgerEntry(
                date(2024, 3, 7), FetchOutcome.ABSENT, "u", datetime.now(UTC), 0, "", 0, None
            )
        )

        report = StockArchiveReporter(store, ledger).build(d1, date(2024, 3, 8))

        assert (report.files, report.rows) == (3, 5)
        assert report.holidays == (date(2024, 3, 7),) and report.unfetched == (date(2024, 3, 8),)
        assert [(c.symbol, c.old, c.new, c.first_seen) for c in report.lot_changes] == [
            ("ABC", 500, 700, d3)
        ]
        assert report.symbols_without_exchange_lot == 1


def test_a_zip_of_the_wrong_shape_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "y")

    import pytest

    from emporos.research.fo_archive_rows import ArchiveParseError

    with pytest.raises(ArchiveParseError):
        StockArchiveReader()(DAY, buffer.getvalue(), ArchiveFormat.UDIFF)
