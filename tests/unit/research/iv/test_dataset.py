"""EM-244: the IV dataset builder and its Parquet store."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from tests.unit.research.iv.test_iv import DAY, EXPIRY, FORWARD, RATE, chain_rows

from emporos.research.iv.dataset import IvBuilder, IvStore


class FlatRate:
    def rate(self, day: date) -> float:
        return RATE


def test_a_day_gives_one_row_per_symbol_and_expiry_kind() -> None:
    rows = IvBuilder(FlatRate()).build_day(DAY, chain_rows())

    (row,) = rows
    assert (row.symbol, row.expiry, row.kind, row.rank) == ("NIFTY", EXPIRY, "monthly", 1)
    assert row.days_to_expiry == 24 and row.forward == FORWARD and row.rate == RATE
    assert row.atm_iv is not None and row.skew25 is not None and row.implied_move is not None


def test_an_expiry_without_a_future_is_a_weekly_with_a_parity_forward() -> None:
    (row,) = IvBuilder(FlatRate()).build_day(DAY, chain_rows(with_future=False))

    assert row.kind == "weekly" and row.forward_source == "parity"


def test_the_store_round_trips_a_day_and_lists_its_days(tmp_path: Path) -> None:
    store = IvStore(tmp_path)
    built = IvBuilder(FlatRate()).build_day(DAY, chain_rows())

    assert store.write(DAY, built) == 1
    assert store.has(DAY) and store.days() == [DAY]
    assert store.read(DAY) == built


def test_a_row_of_another_day_is_refused_by_the_store(tmp_path: Path) -> None:
    built = IvBuilder(FlatRate()).build_day(DAY, chain_rows())

    with pytest.raises(ValueError, match="different day"):
        IvStore(tmp_path).write(date(2024, 6, 4), built)


def test_only_the_nearest_expiries_of_each_kind_are_kept() -> None:
    from tests.unit.research.iv.test_iv import row

    from emporos.research.fo_archive_rows import InstrumentKind

    rows = list(chain_rows())
    for months in (1, 2, 3):
        later = date(2024, 6 + months, 27)
        rows.append(row(InstrumentKind.FUTURE, None, None, FORWARD, expiry=later))
        rows += [
            row(InstrumentKind.OPTION, 22_000.0, side, 300.0 + 40 * months, expiry=later)
            for side in ("CE", "PE")
        ]

    out = IvBuilder(FlatRate()).build_day(DAY, rows)

    assert [r.rank for r in out] == [1, 2] and all(r.kind == "monthly" for r in out)
