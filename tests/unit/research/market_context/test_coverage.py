"""EM-239 L-D3: the coverage of 5-minute bars and of the F&O bhavcopy over the Track L window."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from tests.unit.research.market_context.test_context import day_bars

from emporos.domain.candles import Candle
from emporos.research.market_context.coverage import (
    BarCoverage,
    FoCoverage,
    coverage_lines,
)

DAYS = [date(2026, 3, 2) + timedelta(days=i) for i in range(5)]  # Mon..Fri
REF = "ref"


class Loader:
    def __init__(self, data: dict[str, list[Candle]]) -> None:
        self._data = data

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        return self._data.get(instrument_id, [])


def full(instrument: str, days: Sequence[date], bars: int = 75) -> list[Candle]:
    return [b for d in days for b in day_bars(instrument, d, [10.0 + i * 0.1 for i in range(bars)])]


def world() -> Loader:
    return Loader(
        {
            REF: full(REF, DAYS),
            "full": full("full", DAYS),
            "half": full("half", DAYS[:2]),
            "thin": full("thin", DAYS, bars=40),  # every day short: an illiquid name
        }
    )


class TestBarCoverage:
    def test_a_name_is_covered_by_the_share_of_reference_sessions_it_has_bars_on(self) -> None:
        cover = BarCoverage(world(), REF)
        rows = {
            r.label: r
            for r in cover.of(
                {"full": "FULL", "half": "HALF", "thin": "THIN", "nobars": "NONE"},
                DAYS[0],
                DAYS[-1],
            )  # fmt: skip
        }

        assert (rows["FULL"].sessions, rows["FULL"].expected, rows["FULL"].share) == (5, 5, 1.0)
        assert (rows["HALF"].sessions, rows["HALF"].first_day, rows["HALF"].last_day) == (
            2, DAYS[0], DAYS[1],
        )  # fmt: skip
        assert rows["THIN"].short_sessions == 5  # a fact about the name, counted apart
        assert (rows["NONE"].sessions, rows["NONE"].first_day) == (0, None)

    def test_a_session_the_reference_does_not_have_is_not_counted(self) -> None:
        loader = Loader({REF: full(REF, DAYS[:3]), "x": full("x", DAYS)})

        (row,) = BarCoverage(loader, REF).of({"x": "X"}, DAYS[0], DAYS[-1])

        assert (row.sessions, row.expected) == (3, 3)


class TestFoCoverage:
    def ledger(self, tmp_path: Path, rows: list[tuple[date, str]]) -> Path:
        path = tmp_path / "fo.jsonl"
        path.write_text(
            "\n".join(json.dumps({"day": d.isoformat(), "outcome": o}) for d, o in rows) + "\n",
            encoding="utf-8",
        )
        return path

    def test_it_counts_days_on_disk_absent_days_and_expected_sessions_with_no_record(
        self, tmp_path: Path
    ) -> None:
        path = self.ledger(
            tmp_path,
            [(DAYS[0], "fetched"), (DAYS[1], "absent"), (DAYS[2], "fetched"),
             (date(2020, 1, 1), "fetched")],
        )  # fmt: skip

        fo = FoCoverage.of(path, DAYS, DAYS[0], DAYS[-1])

        assert (fo.fetched, fo.absent) == (2, 1)
        assert fo.missing == (DAYS[3], DAYS[4])
        assert "index futures and options only" in fo.contracts


def test_the_report_names_partial_and_absent_names_and_says_the_fo_archive_is_index_only(
    tmp_path: Path,
) -> None:
    cover = BarCoverage(world(), REF)
    names = cover.of({"full": "FULL", "half": "HALF", "nobars": "NONE"}, DAYS[0], DAYS[-1])
    fo = FoCoverage(546, 32, (), "index futures and options only")

    text = "\n".join(coverage_lines(names, 5, fo, DAYS[0], DAYS[-1]))

    assert "full (>= 99% of sessions): 1; partial: 1; none: 1" in text
    assert "partial  HALF" in text and "none     NONE" in text
    assert "546 days on disk" in text
