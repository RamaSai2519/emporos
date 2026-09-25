"""EM-225: the per-date lot size and expiry calendar, from exchange values where the file has them
and from futures turnover where it does not."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.fo_contract_specs import ContractSpecBuilder, LotSource

D = Decimal
DAY = date(2015, 3, 2)


def future(
    expiry: date, *, lots: int, close: str, lot: int, symbol: str = "NIFTY", exchange: bool = False,
    day: date = DAY,
) -> IndexContractRow:  # fmt: skip
    """A future whose turnover is exactly lots x lot x close, as the exchange reports it."""
    return IndexContractRow(
        day, symbol, InstrumentKind.FUTURE, expiry, None, None, D(1), D(1), D(1), D(close),
        D(close), lots, D(lots) * lot * D(close), 0, 0, None, lot if exchange else None,
        ArchiveFormat.UDIFF if exchange else ArchiveFormat.LEGACY,
    )  # fmt: skip


def option(expiry: date, *, day: date = DAY, symbol: str = "NIFTY") -> IndexContractRow:
    return IndexContractRow(
        day, symbol, InstrumentKind.OPTION, expiry, D(8000), OptionRight.CALL, D(1), D(1), D(1),
        D(1), D(1), 1, D(1), 0, 0, None, None, ArchiveFormat.LEGACY,
    )  # fmt: skip


def test_the_exchange_lot_size_is_used_where_the_file_carries_it() -> None:
    (spec,) = ContractSpecBuilder().build(
        [future(date(2015, 3, 26), lots=500, close="8800", lot=25, exchange=True)]
    )

    assert (spec.lot_size, spec.lot_source, spec.evidence) == (25, LotSource.EXCHANGE, 0)


def test_the_lot_size_is_inferred_from_futures_turnover_and_marked_inferred() -> None:
    rows = [
        future(date(2015, 3, 26), lots=1000, close="8800", lot=25),
        future(date(2015, 4, 30), lots=40, close="8820", lot=25),
    ]

    (spec,) = ContractSpecBuilder().build(rows)

    # the nearest expiry (which the headline lot size is read from) rests on its one future
    assert (spec.lot_size, spec.lot_source, spec.evidence) == (25, LotSource.INFERRED, 1)
    assert spec.expiry_lots == ((date(2015, 3, 26), 25), (date(2015, 4, 30), 25))


def test_a_future_with_too_few_lots_is_not_evidence() -> None:
    (spec,) = ContractSpecBuilder().build([future(date(2015, 3, 26), lots=9, close="8800", lot=25)])

    assert (spec.lot_size, spec.lot_source) == (None, LotSource.UNKNOWN)


def test_a_turnover_rounded_to_lakhs_still_infers_the_right_lot() -> None:
    # the real BANKNIFTY future of 2015-03-02: 934,308.79 lakh over 186,110 lots at 20,174.25
    row = future(date(2015, 3, 26), lots=186_110, close="20174.25", lot=25)
    rounded = IndexContractRow(**{**row.__dict__, "turnover": D("934308.79") * 100_000})

    (spec,) = ContractSpecBuilder().build([rounded])

    assert spec.lot_size == 25  # turnover / (lots x close) is 24.88 on the real file: rounds to 25


def test_the_calendar_lists_each_distinct_expiry_of_each_kind_sorted() -> None:
    rows = [
        future(date(2015, 4, 30), lots=100, close="8800", lot=25),
        future(date(2015, 3, 26), lots=100, close="8800", lot=25),
        option(date(2015, 3, 26)),
        option(date(2015, 3, 26)),
        option(date(2015, 5, 28)),
    ]

    (spec,) = ContractSpecBuilder().build(rows)

    assert spec.future_expiries == (date(2015, 3, 26), date(2015, 4, 30))
    assert spec.option_expiries == (date(2015, 3, 26), date(2015, 5, 28))


def test_each_day_and_symbol_gets_its_own_spec_oldest_first() -> None:
    later = date(2015, 3, 3)
    rows = [
        future(date(2015, 3, 26), lots=100, close="8800", lot=25, day=later),
        future(date(2015, 3, 26), lots=100, close="18000", lot=20, symbol="BANKNIFTY"),
        future(date(2015, 3, 26), lots=100, close="8800", lot=25),
    ]

    specs = ContractSpecBuilder().build(rows)

    assert [(s.day, s.symbol, s.lot_size) for s in specs] == [
        (DAY, "BANKNIFTY", 20),
        (DAY, "NIFTY", 25),
        (later, "NIFTY", 25),
    ]


def test_two_lot_sizes_for_one_expiry_in_one_file_are_refused() -> None:
    rows = [
        future(date(2015, 3, 26), lots=100, close="8800", lot=25, exchange=True),
        future(date(2015, 3, 26), lots=90, close="8800", lot=50, exchange=True),
    ]

    with pytest.raises(ValueError, match="two lot sizes"):
        ContractSpecBuilder().build(rows)


def test_different_expiries_may_carry_different_lot_sizes() -> None:
    """FINNIFTY on 2024-07-08: the exchange changes a lot size for NEW expiries only."""
    rows = [
        future(date(2015, 3, 26), lots=100, close="8800", lot=40, exchange=True),
        future(date(2015, 4, 30), lots=100, close="8800", lot=25, exchange=True),
        option(date(2015, 3, 26)),
    ]

    (spec,) = ContractSpecBuilder().build(rows)

    assert spec.expiry_lots == ((date(2015, 3, 26), 40), (date(2015, 4, 30), 25))
    assert (spec.lot_size, spec.lot_for(date(2015, 4, 30))) == (40, 25)  # headline is the nearest
    assert spec.lot_for(date(2016, 1, 1)) == 40  # an expiry with none listed falls back


def test_the_check_compares_inference_with_the_exchange_value_where_both_exist() -> None:
    good = future(date(2015, 3, 26), lots=500, close="8800", lot=25, exchange=True)
    wrong = IndexContractRow(**{**good.__dict__, "day": date(2015, 3, 3), "lot_size": 50})

    checks = ContractSpecBuilder().check([good, wrong])

    assert [(c.day, c.exchange, c.inferred, c.agrees) for c in checks] == [
        (DAY, 25, 25, True),
        (date(2015, 3, 3), 50, 25, False),
    ]


def test_specs_round_trip_through_parquet(tmp_path: object) -> None:
    from pathlib import Path

    from emporos.research.fo_contract_specs import read_specs, write_specs

    specs = ContractSpecBuilder().build(
        [
            future(date(2015, 3, 26), lots=500, close="8800", lot=25),
            option(date(2015, 3, 26)),
            option(date(2015, 5, 28)),
            future(date(2015, 3, 26), lots=3, close="18000", lot=20, symbol="BANKNIFTY"),
        ]
    )
    path = Path(str(tmp_path)) / "specs.parquet"

    write_specs(path, specs)

    assert read_specs(path) == specs


@pytest.mark.parametrize(("close", "lot"), [("8800", 75), ("8800", 120), ("8800", 50)])
def test_a_turnover_a_little_off_snaps_to_the_nearest_multiple_of_five(
    close: str, lot: int
) -> None:
    exact = future(date(2015, 3, 26), lots=1000, close=close, lot=lot)
    off = IndexContractRow(
        **{**exact.__dict__, "turnover": exact.turnover * D("1.015")}
    )  # 1.5% high

    (spec,) = ContractSpecBuilder().build([off])

    assert spec.lot_size == lot
