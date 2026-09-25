"""EM-225/226: the stored archive as a chain source: the lot size in force, the index level from the
file or the index series, and a stale close never taken for a price."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_chain_source import BhavcopyChainSource
from emporos.research.fo_contract_specs import ContractSpec, ContractSpecBuilder
from emporos.research.index_daily import daily_closes

D = Decimal
DAY = date(2024, 9, 30)
EXPIRY, NEXT = date(2024, 10, 31), date(2024, 11, 28)


def option(
    strike: str, right: OptionRight = OptionRight.PUT, *, day: date = DAY, symbol: str = "NIFTY",
    close: str = "89.55", settle: str = "89.55", lots: int = 4171, level: str | None = "25810.85",
    lot: int | None = 25, expiry: date = EXPIRY,
) -> IndexContractRow:  # fmt: skip
    return IndexContractRow(
        day, symbol, InstrumentKind.OPTION, expiry, D(strike), right, D(1), D(1), D(1), D(close),
        D(settle), lots, D(0), 24400, 0, None if level is None else D(level), lot,
        ArchiveFormat.UDIFF if lot is not None else ArchiveFormat.LEGACY,
    )  # fmt: skip


def future(day: date = DAY, symbol: str = "NIFTY", lot: int | None = 25) -> IndexContractRow:
    return IndexContractRow(
        day, symbol, InstrumentKind.FUTURE, NEXT, None, None, D(1), D(1), D(1), D(26000), D(26000),
        500, D(500) * (lot or 25) * D(26000), 0, 0, None, lot,
        ArchiveFormat.UDIFF if lot is not None else ArchiveFormat.LEGACY,
    )  # fmt: skip


def make(
    tmp_path: Path,
    days: dict[date, list[IndexContractRow]],
    closes: dict[date, Decimal] | None = None,
) -> tuple[BhavcopyChainSource, FoDayStore]:  # fmt: skip
    store = FoDayStore(tmp_path)
    specs: list[ContractSpec] = []
    for day, rows in days.items():
        store.write(day, rows)
        specs += ContractSpecBuilder().build(rows)
    return BhavcopyChainSource(store, specs, "NIFTY", D(50), closes or {}), store


def test_a_day_becomes_a_snapshot_with_its_lot_size_level_and_expiries(tmp_path: Path) -> None:
    source, _ = make(
        tmp_path,
        {DAY: [option("24950"), option("25000", OptionRight.CALL, expiry=NEXT), future()]},
    )

    snap = source.snapshot(DAY)

    assert snap is not None
    assert (snap.lot_size, snap.underlying_close, snap.strike_step) == (25, D("25810.85"), D(50))
    assert snap.expiry_dates == (EXPIRY, NEXT)  # futures rows are not part of a chain
    quote = snap.expiries[EXPIRY].quote(D("24950"), OptionRight.PUT)
    assert quote is not None and (quote.close, quote.contracts_traded) == (D("89.55"), 4171)


def test_another_underlyings_rows_are_left_out(tmp_path: Path) -> None:
    source, _ = make(tmp_path, {DAY: [option("24950"), option("50000", symbol="BANKNIFTY")]})

    snap = source.snapshot(DAY)

    assert snap is not None and snap.expiries[EXPIRY].strikes == (D("24950"),)


def test_a_contract_that_did_not_trade_is_not_a_price(tmp_path: Path) -> None:
    source, _ = make(tmp_path, {DAY: [option("27300", lots=0, close="980.55", settle="1416.40")]})

    snap = source.snapshot(DAY)

    assert snap is not None
    quote = snap.expiries[EXPIRY].quote(D("27300"), OptionRight.PUT)
    assert quote is not None and not quote.tradable and quote.mark == D("1416.40")


def test_a_legacy_day_takes_its_level_from_the_index_series(tmp_path: Path) -> None:
    legacy = date(2015, 3, 2)
    rows = [option("8500", day=legacy, level=None, lot=None), future(legacy, lot=None)]
    source, _ = make(tmp_path, {legacy: rows}, {legacy: D("8800.5")})

    snap = source.snapshot(legacy)

    assert snap is not None and snap.underlying_close == D("8800.5")
    assert snap.lot_size == 25  # inferred from the future's turnover


def test_a_legacy_day_with_no_index_close_or_no_lot_size_is_not_offered(tmp_path: Path) -> None:
    legacy, other = date(2015, 3, 2), date(2015, 3, 3)
    rows = {
        legacy: [option("8500", day=legacy, level=None, lot=None), future(legacy, lot=None)],
        other: [option("8500", day=other, level=None, lot=None)],  # no future: no lot size
    }
    source, _ = make(tmp_path, rows, {other: D("8800")})

    assert list(source.days()) == []
    assert source.snapshot(legacy) is None and source.snapshot(other) is None


def test_days_are_oldest_first_and_only_stored_ones(tmp_path: Path) -> None:
    later = DAY + timedelta(days=1)
    source, _ = make(tmp_path, {later: [option("24950", day=later)], DAY: [option("24950")]})

    assert list(source.days()) == [DAY, later]


def test_the_archive_level_is_checked_against_the_index_series(tmp_path: Path) -> None:
    source, _ = make(tmp_path, {DAY: [option("24950")]}, {DAY: D("25811")})

    assert source.underlying_agreement(D("0.001")) == []
    off = source.underlying_agreement(D("0.00000001"))
    assert off == [(DAY, D("25810.85"), D("25811"))]


def test_a_bad_step_or_tick_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BhavcopyChainSource(FoDayStore(tmp_path), [], "NIFTY", D(0), {})


def bars_for(day: date, count: int, *, last_start_minute: int = 25) -> list[Candle]:
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)
    return [
        Candle(
            "NSE:99926000", Timeframe.M5, start + timedelta(minutes=5 * i), Money.of(100 + i),
            Money.of(200), Money.of(50), Money.of(100 + i), 0,
        )
        for i in range(count)
    ]  # fmt: skip


class TestDailyCloses:
    def test_a_whole_session_closes_at_its_last_bars_close(self) -> None:
        closes = daily_closes(bars_for(DAY, 75))

        assert closes == {DAY: D(174)}  # the 75th bar starts 15:25 and closes at 100 + 74

    def test_a_session_that_stops_early_has_no_close(self) -> None:
        assert daily_closes(bars_for(DAY, 74)) == {}
