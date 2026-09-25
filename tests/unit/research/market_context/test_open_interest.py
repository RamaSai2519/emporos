"""EM-239: F&O open-interest lines from the stock bhavcopy; only files before the day are read."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.research.market_context.test_context import NIFTY, SESSIONS, VIX, Loader, series

from emporos.core.clock import IST
from emporos.eventtrader.events import MarketEvent
from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.market_context.bars import BarSeriesCache
from emporos.research.market_context.builder import CONTEXT_KEYS, AsOfContextBuilder, SectorMap
from emporos.research.market_context.open_interest import FoOpenInterest

DAYS = [date(2026, 3, 2) + timedelta(days=i) for i in range(20) if (i % 7) < 5]
NEAR, NEXT = date(2026, 3, 26), date(2026, 4, 30)


def future(
    day: date, expiry: date, oi: int, settle: str, underlying: str | None = "100"
) -> IndexContractRow:
    return IndexContractRow(
        day, "ABB", InstrumentKind.FUTURE, expiry, None, None, Decimal(settle), Decimal(settle),
        Decimal(settle), Decimal(settle), Decimal(settle), 10, Decimal(1000), oi, 0,
        None if underlying is None else Decimal(underlying), 100, ArchiveFormat.UDIFF,
    )  # fmt: skip


def option(day: date, right: OptionRight, oi: int, strike: str = "100") -> IndexContractRow:
    return IndexContractRow(
        day, "ABB", InstrumentKind.OPTION, NEAR, Decimal(strike), right, Decimal(1), Decimal(1),
        Decimal(1), Decimal(1), Decimal(1), 10, Decimal(1000), oi, 0, Decimal(100), 100,
        ArchiveFormat.UDIFF,
    )  # fmt: skip


def store(tmp_path: Path, poison_from: date | None = None) -> FoDayStore:
    """Day n: near-month OI 1000 + 100 n, settle 100 + n; next-month OI 500; puts 300, calls 200.
    From `poison_from` every number is absurd."""
    files = FoDayStore(tmp_path)
    for n, day in enumerate(DAYS):
        bad = poison_from is not None and day >= poison_from
        oi = 10**9 if bad else 1000 + 100 * n
        settle = "9999" if bad else str(100 + n)
        files.write(
            day,
            [
                future(day, NEAR, oi, settle),
                future(day, NEXT, 500, settle),
                option(day, OptionRight.PUT, 300),
                option(day, OptionRight.CALL, 200),
            ],
        )
    return files


LAST = DAYS[9]  # the bhavcopy before the decision day
DECISION_DAY = DAYS[10]


class TestLines:
    def test_the_numbers_from_the_last_bhavcopy_before_the_day(self, tmp_path: Path) -> None:
        got = FoOpenInterest(store(tmp_path)).lines("ABB", DECISION_DAY)
        assert got["oi_asof"] == LAST.isoformat()
        assert got["fut_open_interest"] == 1900 + 500  # near 1000 + 100 * 9, next 500
        assert got["fut_oi_change_pct"] == pytest.approx((2400 / 2300 - 1) * 100)
        five_before = [1000 + 100 * n + 500 for n in range(4, 9)]
        assert got["fut_oi_vs_5d_avg"] == pytest.approx(2400 / (sum(five_before) / 5))
        assert got["fut_settle_change_pct"] == pytest.approx((109 / 108 - 1) * 100)
        assert got["fut_basis_pct"] == pytest.approx(9.0)
        assert got["put_call_oi_ratio"] == pytest.approx(1.5)

    def test_the_decision_days_own_file_and_later_ones_are_unreachable(
        self, tmp_path: Path
    ) -> None:
        clean = FoOpenInterest(store(tmp_path / "clean")).lines("ABB", DECISION_DAY)
        dirty = FoOpenInterest(store(tmp_path / "dirty", poison_from=DECISION_DAY)).lines(
            "ABB", DECISION_DAY
        )
        assert clean == dirty and clean

    def test_a_name_with_no_futures_or_a_stale_file_has_no_lines(self, tmp_path: Path) -> None:
        oi = FoOpenInterest(store(tmp_path))
        assert oi.lines("XYZ", DECISION_DAY) == {}
        assert oi.lines("ABB", DAYS[0]) == {}  # nothing before the first file
        assert oi.lines("ABB", DAYS[-1] + timedelta(days=30)) == {}

    def test_a_legacy_file_has_no_basis_and_no_calls_means_no_ratio(self, tmp_path: Path) -> None:
        files = FoDayStore(tmp_path)
        for day in DAYS[:3]:
            files.write(day, [future(day, NEAR, 1000, "100", underlying=None)])
        got = FoOpenInterest(files).lines("ABB", DAYS[2] + timedelta(days=1))
        assert "fut_basis_pct" not in got and "put_call_oi_ratio" not in got
        assert got["fut_open_interest"] == 1000.0

    def test_the_expiring_contract_is_not_the_near_month(self, tmp_path: Path) -> None:
        files = FoDayStore(tmp_path)
        files.write(DAYS[0], [future(DAYS[0], NEAR, 900, "100"), future(DAYS[0], NEXT, 500, "101")])
        files.write(DAYS[1], [future(DAYS[1], DAYS[1], 0, "50"), future(DAYS[1], NEXT, 600, "102")])
        got = FoOpenInterest(files).lines("ABB", DAYS[2])
        assert got["fut_settle_change_pct"] == pytest.approx((102 / 101 - 1) * 100)


class TestInTheContext:
    def builder(self, tmp_path: Path, oi: FoOpenInterest | None) -> AsOfContextBuilder:
        data = {
            "NSE:1": series("NSE:1", 100, 1),
            NIFTY: series(NIFTY, 200, 2),
            VIX: series(VIX, 15, 0.1),
        }
        cache = BarSeriesCache(Loader(data), SESSIONS[0], SESSIONS[-1])
        return AsOfContextBuilder(cache, NIFTY, VIX, SectorMap({}), oi)

    def event(self, at: datetime, instrument_id: str = "NSE:1") -> MarketEvent:
        return MarketEvent("NSE:1", instrument_id, "ABB", at, at, "filing", "Updates", "s", "t")

    def test_the_keys_are_added_and_are_a_subset_of_the_declared_ones(self, tmp_path: Path) -> None:
        at = datetime.combine(DECISION_DAY, time(11, 0), tzinfo=IST)
        oi = FoOpenInterest(store(tmp_path))
        with_oi = self.builder(tmp_path, oi).context(self.event(at), at).lines
        without = self.builder(tmp_path, None).context(self.event(at), at).lines
        assert set(with_oi) - set(without) == {
            "oi_asof", "fut_open_interest", "fut_oi_change_pct", "fut_oi_vs_5d_avg",
            "fut_settle_change_pct", "fut_basis_pct", "put_call_oi_ratio",
        }  # fmt: skip
        assert set(with_oi) <= set(CONTEXT_KEYS)

    def test_a_name_with_no_token_still_gets_its_oi(self, tmp_path: Path) -> None:
        at = datetime.combine(DECISION_DAY, time(11, 0), tzinfo=IST)
        lines = (
            self.builder(tmp_path, FoOpenInterest(store(tmp_path)))
            .context(self.event(at, instrument_id=""), at)
            .lines
        )
        assert lines["fut_open_interest"] == 2400.0
