"""EM-243: the statistics, the report text, the Parquet files, the panel and the series cache."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from emporos.core.clock import IST
from emporos.research.atlas.crossings import CrossingBlock
from emporos.research.atlas.events import EventClass, MoveEvent, OnsetKind
from emporos.research.atlas.files import write_crossings, write_moves
from emporos.research.atlas.panel import PanelBuilder, slot_end_minute
from emporos.research.atlas.report import crossing_lines, move_lines, names_csv
from emporos.research.atlas.series import BarSeries, CachedSeriesSource, CandleSeriesSource
from emporos.research.atlas.stats import clustered_mean_t

D = date(2024, 3, 4)
HORIZONS = ("15m", "60m", "1515", "1d", "3d", "5d")


def event(cls: EventClass = EventClass.STOCK_DAILY, day: date = D, onset: int = 10) -> MoveEvent:
    at = datetime(day.year, day.month, day.day, 10, onset, tzinfo=IST)
    return MoveEvent(
        f"e-{cls.value}-{day}", cls, "AAA", "NSE:1", "NIFTY IT", "tata", day, 1, 2.0, 1.5, 0.4, 3.7,
        0.1, OnsetKind.INTRADAY, at, at, 0.2, 0.3, 0.5, (1.0, 2.0, 3.0, 4.0), (0.5, 1.0, 1.5, 2.0),
        1.1, 0.8, 0.3,
    )  # fmt: skip


class TestStats:
    def test_the_mean_the_hit_rate_and_a_day_clustered_t(self) -> None:
        values = np.array([1.0, 1.0, 1.0, -0.5])
        days = [D, D, date(2024, 3, 5), date(2024, 3, 6)]

        c = clustered_mean_t(values, days)

        assert (c.n, round(c.mean, 4), c.hit) == (4, 0.625, 0.75)
        assert c.t > 0 and "n=" in c.line()

    def test_one_crowded_day_is_not_many_independent_crossings(self) -> None:
        rng = np.random.default_rng(0)
        days = [date(2024, 1, 1 + i // 50) for i in range(250)]
        common = np.repeat(rng.normal(0, 1, 5), 50)
        values = common + rng.normal(0, 0.1, 250) + 0.3

        naive = values.mean() / (values.std(ddof=1) / np.sqrt(len(values)))
        assert clustered_mean_t(values, days).t < 0.2 * naive  # 250 crossings, but five days

    def test_nothing_to_measure_is_n_zero(self) -> None:
        c = clustered_mean_t(np.array([np.nan]), [D])

        assert c.n == 0 and c.line() == "n=0"


class TestReports:
    def test_counts_per_month_class_and_name_and_the_onset_distribution(self) -> None:
        events = [event(), event(day=date(2024, 4, 1)), event(EventClass.MARKET)]

        text = "\n".join(move_lines(events))

        assert "Move Ledger: 3 events" in text and "2024-03" in text and "2024-04" in text
        assert "AAA" in text and "intraday onsets by half hour" in text
        assert names_csv(events).splitlines()[0].startswith("name,stock_daily")
        assert names_csv(events).splitlines()[1] == "AAA,2,0,0,1,0"

    def test_the_crossing_baseline_lists_every_horizon(self) -> None:
        block = CrossingBlock()
        for i in range(30):
            block.add(
                kind="stock", name="AAA", instrument_id="NSE:1", sector="", group="",
                day=date(2024, 1, 1 + i), direction=1, k=1.5, at_open=bool(i % 2),
                crossed_minute=600, entry_minute=605, move_so_far_pct=0.5, gap_pct=0.1,
                level_pct=0.4, sigma_pct=0.3, entry_price=100.0, slip_pct=0.05,
                **{f"resid_{h}_pct": 0.1 * (i % 5) for h in HORIZONS},
                **{f"raw_{h}_pct": 0.2 for h in HORIZONS},
            )  # fmt: skip

        text = "\n".join(crossing_lines(block))

        assert "stock (k=1.5): n=30" in text and "50.0% already there at the open" in text
        assert "resid to 1515" in text and "raw   to 5d" in text
        assert "by year: 2024" in text
        assert "Crossings per month and kind:" in text and "2024-01  " in text
        assert "stock@1.5" in text


class TestFiles:
    def test_moves_and_crossings_are_written_as_parquet(self, tmp_path: Path) -> None:
        n = write_moves(tmp_path / "m.parquet", [event(), event(EventClass.PLACEBO)])
        table = pq.read_table(tmp_path / "m.parquet")

        assert n == 2 and table.num_rows == 2
        assert {"onset_kind", "fwd_raw_3d_pct", "fwd_resid_10d_pct", "beta_group"} <= set(
            table.column_names
        )

        block = CrossingBlock()
        block.add(
            kind="swing_stock", name="AAA", instrument_id="NSE:1", sector="", group="", day=D,
            direction=-1, k=0.0, at_open=False, crossed_minute=930, entry_minute=555,
            move_so_far_pct=2.1, gap_pct=0.3, level_pct=2.0, sigma_pct=1.0, entry_price=99.5,
            slip_pct=0.0,
            **{f"resid_{h}_pct": float("nan") for h in HORIZONS},
            **{f"raw_{h}_pct": 0.1 for h in HORIZONS},
        )  # fmt: skip
        assert write_crossings(tmp_path / "c.parquet", block) == 1
        assert pq.read_table(tmp_path / "c.parquet").schema.field("day").type == "date32[day]"


class TestPanelAndSeries:
    def test_bars_land_in_their_session_and_slot(self) -> None:
        day = date(2024, 3, 4)
        start = int(datetime(2024, 3, 4, 9, 15, tzinfo=IST).timestamp())
        series = BarSeries(
            np.array([start, start + 300, start + 74 * 300, start + 999_999], dtype=np.int64),
            np.array([10.0, 11.0, 12.0, 13.0]), np.array([10.5, 11.5, 12.5, 13.5]),
        )  # fmt: skip

        panel = PanelBuilder((day,)).build(series)

        assert panel.open0[0] == 10.0 and panel.close[0, 0] == 10.5 and panel.close[0, 74] == 12.5
        assert panel.day_close[0] == 12.5 and np.isnan(panel.close[0, 30])
        assert slot_end_minute(0) == 9 * 60 + 20

    def test_a_series_is_loaded_once_and_then_read_from_its_cache(self, tmp_path: Path) -> None:
        calls: list[str] = []

        class Inner:
            def series(self, instrument_id: str) -> BarSeries:
                calls.append(instrument_id)
                return BarSeries(np.array([1], dtype=np.int64), np.array([2.0]), np.array([3.0]))

        cached = CachedSeriesSource(Inner(), tmp_path, "tag1")
        first, second = cached.series("NSE:1"), cached.series("NSE:1")
        CachedSeriesSource(Inner(), tmp_path, "tag2").series("NSE:1")

        assert calls == ["NSE:1", "NSE:1"]  # the second tag is a different file
        assert first.close[0] == second.close[0] == 3.0

    def test_candles_become_arrays_oldest_first(self) -> None:
        from decimal import Decimal

        from emporos.domain.candles import Candle, Timeframe
        from emporos.domain.money import Money

        def candle(minute: int, price: str) -> Candle:
            ts = datetime(2024, 3, 4, 4, minute, tzinfo=IST).astimezone(__import__("datetime").UTC)
            p = Money(Decimal(price))
            return Candle("NSE:1", Timeframe.M5, ts, p, p, p, p, 1)

        class Loader:
            def load(self, instrument_id: str, first: date, last: date) -> list[Candle]:
                return [candle(10, "2"), candle(5, "1")]

        series = CandleSeriesSource(Loader(), D, D).series("NSE:1")

        assert list(series.close) == [1.0, 2.0] and series.ts[0] < series.ts[1]
