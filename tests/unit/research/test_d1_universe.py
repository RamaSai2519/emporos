"""EM-214: the D1 research universe. Holdout, liquidity, corporate-action quarantine, the seam."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest
from tests.unit.research.test_shock_reversal import FRIDAY, INSTRUMENT, MONDAY, session

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cache import CandleCacheFiles, ColdArchiveFiles, FileCandleReader
from emporos.persistence.candle_cold import ParquetCandleCodec
from emporos.research.cell_run import ScreenUniverse
from emporos.research.d1_universe import (
    D1Manifest,
    D1Profiler,
    D1Universe,
    D1UniverseBuilder,
    LiquidityRule,
    SeededHoldout,
)
from emporos.research.partition import DISCOVERY, DataSplit

STEADY = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
    MONDAY, open_="100", at_hour="100", close="100"
)


def thin(bars: list[Candle], volume: int) -> list[Candle]:
    return [replace(bar, volume=volume) for bar in bars]


def rescaled(bars: list[Candle], instrument_id: str) -> list[Candle]:
    return [replace(bar, instrument_id=instrument_id) for bar in bars]


class TestProfiler:
    def test_sessions_and_the_median_daily_traded_value(self) -> None:
        profile = D1Profiler().profile(INSTRUMENT, STEADY)

        assert profile.sessions == 2
        assert profile.median_daily_value == Decimal(100) * 1_000_000 * 75  # close x volume x bars

    def test_a_split_shaped_open_is_a_discontinuity_on_that_day(self) -> None:
        halved = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="50", at_hour="50", close="50"
        )

        assert D1Profiler().profile(INSTRUMENT, halved).discontinuities == (MONDAY,)

    def test_a_large_but_ordinary_gap_is_not_one(self) -> None:
        gapped = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="110", at_hour="110", close="110"
        )

        assert D1Profiler().profile(INSTRUMENT, gapped).discontinuities == ()

    def test_no_bars_is_an_empty_profile_not_a_crash(self) -> None:
        profile = D1Profiler().profile(INSTRUMENT, [])

        assert (profile.sessions, profile.median_daily_value) == (0, Decimal(0))


class TestLiquidityRule:
    RULE = LiquidityRule(Decimal(1_000_000), 2)

    def test_a_deep_name_with_enough_sessions_is_kept(self) -> None:
        assert self.RULE.reason_to_exclude(D1Profiler().profile(INSTRUMENT, STEADY)) is None

    def test_too_few_sessions_is_a_reason(self) -> None:
        one_day = session(FRIDAY, open_="100", at_hour="100", close="100")

        reason = self.RULE.reason_to_exclude(D1Profiler().profile(INSTRUMENT, one_day))

        assert reason is not None and "1 sessions" in reason

    def test_a_thin_name_is_a_reason(self) -> None:
        reason = self.RULE.reason_to_exclude(D1Profiler().profile(INSTRUMENT, thin(STEADY, 1)))

        assert reason is not None and "median daily value" in reason

    def test_the_shipped_rule_is_ten_crore_and_two_years(self) -> None:
        rule = LiquidityRule()

        assert (rule.min_median_daily_value, rule.min_sessions) == (Decimal(100_000_000), 500)


class TestSeededHoldout:
    NAMES: ClassVar[list[str]] = [f"NSE:{n}" for n in range(1, 201)]

    def test_it_takes_the_fraction(self) -> None:
        assert len(SeededHoldout("s", Decimal("0.3")).pick(self.NAMES)) == 60

    def test_the_same_seed_and_names_give_the_same_set_in_any_order(self) -> None:
        holdout = SeededHoldout("s", Decimal("0.3"))

        assert holdout.pick(self.NAMES) == holdout.pick(reversed(self.NAMES))

    def test_another_seed_gives_another_set(self) -> None:
        assert SeededHoldout("a", Decimal("0.3")).pick(self.NAMES) != SeededHoldout(
            "b", Decimal("0.3")
        ).pick(self.NAMES)

    def test_a_name_added_later_does_not_reshuffle_the_rest_of_the_ranking(self) -> None:
        holdout = SeededHoldout("s", Decimal("0.3"))
        before = holdout.pick(self.NAMES)
        after = holdout.pick([*self.NAMES, "NSE:999"])

        assert len(before - after) <= 1  # a hash rank moves only the boundary name

    @pytest.mark.parametrize("fraction", ["0", "1", "-0.1", "1.5"])
    def test_a_fraction_outside_zero_and_one_is_refused(self, fraction: str) -> None:
        with pytest.raises(ValueError, match="strictly between"):
            SeededHoldout("s", Decimal(fraction))

    def test_a_blank_seed_is_refused(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            SeededHoldout(" ", Decimal("0.3"))


class RecordingBars:
    """A `BarSource` that remembers which names were asked for."""

    def __init__(self, bars: dict[str, list[Candle]]) -> None:
        self._bars = bars
        self.asked: list[str] = []

    def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
        self.asked.append(instrument_id)
        return self._bars.get(instrument_id, [])


def builder(fraction: str = "0.5") -> D1UniverseBuilder:
    return D1UniverseBuilder(
        "seed", Decimal(fraction), LiquidityRule(Decimal(1_000_000), 2), D1Profiler()
    )


class TestBuilder:
    IDS: ClassVar[list[str]] = [f"NSE:{n}" for n in range(1, 11)]

    def build(
        self, bars: RecordingBars, audited: Sequence[str] = ("NSE:1",), fraction: str = "0.5"
    ) -> D1Manifest:
        return builder(fraction).build(self.IDS, audited, [], bars)

    def test_a_held_out_name_is_never_asked_for(self) -> None:
        bars = RecordingBars({i: rescaled(STEADY, i) for i in self.IDS})

        manifest = self.build(bars)

        assert manifest.holdout and not set(manifest.holdout) & set(bars.asked)

    def test_an_audited_name_is_never_held_out(self) -> None:
        for fraction in ("0.3", "0.5", "0.9"):
            manifest = self.build(RecordingBars({}), fraction=fraction)

            assert "NSE:1" not in manifest.holdout

    def test_the_holdout_is_a_fraction_of_the_names_outside_the_audit(self) -> None:
        manifest = self.build(RecordingBars({}), audited=("NSE:1", "NSE:2"))

        assert len(manifest.holdout) == 4  # half of the eight unaudited names

    def test_thin_and_short_names_are_excluded_with_the_reason_and_the_rest_included(self) -> None:
        good, thin_id, short_id = "NSE:1", "NSE:2", "NSE:3"
        bars = RecordingBars(
            {
                good: rescaled(STEADY, good),
                thin_id: rescaled(thin(STEADY, 1), thin_id),
                short_id: rescaled(
                    session(FRIDAY, open_="100", at_hour="100", close="100"), short_id
                ),
            }
        )

        manifest = builder("0.1").build([good, thin_id, short_id], [good], [], bars)

        assert manifest.included == (good,)
        assert set(manifest.excluded) == {thin_id, short_id}

    def test_a_discontinuity_is_quarantined_for_an_included_name(self) -> None:
        halved = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="50", at_hour="50", close="50"
        )
        bars = RecordingBars({"NSE:1": rescaled(halved, "NSE:1")})

        manifest = builder("0.1").build(["NSE:1"], ["NSE:1"], [("NSE:9", date(2020, 1, 1))], bars)

        assert manifest.quarantined == (("NSE:1", MONDAY), ("NSE:9", date(2020, 1, 1)))

    def test_only_the_profiled_split_is_read(self) -> None:
        seen: list[DataSplit] = []

        class Spy:
            def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
                seen.append(split)
                return []

        builder("0.1").build(["NSE:1"], ["NSE:1"], [], Spy())

        assert seen == [DISCOVERY]


class TestManifestAndUniverse:
    def manifest(self) -> D1Manifest:
        bars = RecordingBars({f"NSE:{n}": rescaled(STEADY, f"NSE:{n}") for n in range(1, 7)})
        return builder("0.5").build([f"NSE:{n}" for n in range(1, 7)], ["NSE:1"], [], bars)

    def test_it_round_trips_through_its_file(self, tmp_path: Path) -> None:
        manifest = self.manifest()
        manifest.save(tmp_path / "u.yaml")

        assert D1Manifest.load(tmp_path / "u.yaml") == manifest

    def test_a_hand_edited_file_is_refused(self, tmp_path: Path) -> None:
        self.manifest().save(tmp_path / "u.yaml")
        text = (tmp_path / "u.yaml").read_text()
        (tmp_path / "u.yaml").write_text(text.replace("seed: seed", "seed: other"))

        with pytest.raises(ConfigurationError, match="edited by hand"):
            D1Manifest.load(tmp_path / "u.yaml")

    def test_a_missing_file_is_a_configuration_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            D1Manifest.load(tmp_path / "absent.yaml")

    def test_the_universe_is_the_included_names_and_skips_quarantined_days(self) -> None:
        manifest = replace(self.manifest(), quarantined=(("NSE:1", MONDAY),))
        universe: ScreenUniverse = D1Universe(manifest)

        assert tuple(universe.instrument_ids) == manifest.included
        assert universe.is_quarantined("NSE:1", MONDAY)
        assert not universe.is_quarantined("NSE:1", FRIDAY)

    def test_the_label_names_the_size_and_changes_with_the_content(self) -> None:
        first = D1Universe(self.manifest())
        other = D1Universe(replace(self.manifest(), seed="another"))

        assert first.universe_label.startswith(f"d1-{len(first.instrument_ids)}-")
        assert first.universe_label != other.universe_label

    def test_the_holdout_never_reaches_the_universe(self) -> None:
        universe = D1Universe(self.manifest())

        assert not set(universe.instrument_ids) & set(self.manifest().holdout)


class TestColdArchiveFiles:
    def write_month(self, root: Path, instrument_id: str, bars: list[Candle]) -> None:
        path = root / "candles" / "5m" / instrument_id / "2026-01.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(ParquetCandleCodec().encode(bars))

    def test_it_reads_the_archives_own_layout_with_the_colon_kept(self, tmp_path: Path) -> None:
        files = ColdArchiveFiles(tmp_path)

        assert files.path("NSE:1", "5m", "2026-01") == (
            tmp_path / "candles" / "5m" / "NSE:1" / "2026-01.parquet"
        )

    def test_a_file_candle_reader_reads_the_archive_through_it(self, tmp_path: Path) -> None:
        cache, cold = tmp_path / "cache", tmp_path / "cold"
        self.write_month(cold, INSTRUMENT, STEADY)
        reader = FileCandleReader([CandleCacheFiles(cache), ColdArchiveFiles(cold)])
        start = datetime(2026, 1, 1, tzinfo=IST)

        bars = asyncio.run(
            reader.get_range(INSTRUMENT, Timeframe.M5, start, start + timedelta(days=31))
        )

        assert len(bars) == len(STEADY)

    def test_the_cache_layer_wins_when_it_has_the_month(self, tmp_path: Path) -> None:
        cache, cold = tmp_path / "cache", tmp_path / "cold"
        self.write_month(cold, INSTRUMENT, STEADY)
        cached = CandleCacheFiles(cache)
        cached.write(
            cached.path(INSTRUMENT, "5m", "2026-01"), ParquetCandleCodec().encode(STEADY[:5])
        )
        reader = FileCandleReader([cached, ColdArchiveFiles(cold)])
        start = datetime(2026, 1, 1, tzinfo=IST)

        bars = asyncio.run(
            reader.get_range(INSTRUMENT, Timeframe.M5, start, start + timedelta(days=31))
        )

        assert len(bars) == 5

    def test_it_can_neither_write_nor_clear_the_archive(self, tmp_path: Path) -> None:
        self.write_month(tmp_path, INSTRUMENT, STEADY)
        files = ColdArchiveFiles(tmp_path)

        with pytest.raises(PermissionError):
            files.write(files.path(INSTRUMENT, "5m", "2026-01"), b"x")
        with pytest.raises(PermissionError):
            files.clear()
        assert files.path(INSTRUMENT, "5m", "2026-01").is_file()

    def test_without_memoizing_no_month_is_held_after_it_is_read(self, tmp_path: Path) -> None:
        self.write_month(tmp_path, INSTRUMENT, STEADY)
        reader = FileCandleReader([ColdArchiveFiles(tmp_path)], memoize=False)
        start = datetime(2026, 1, 1, tzinfo=IST)
        end = start + timedelta(days=31)

        first = asyncio.run(reader.get_range(INSTRUMENT, Timeframe.M5, start, end))
        (tmp_path / "candles" / "5m" / INSTRUMENT / "2026-01.parquet").write_bytes(
            ParquetCandleCodec().encode(STEADY[:5])
        )
        second = asyncio.run(reader.get_range(INSTRUMENT, Timeframe.M5, start, end))

        assert (len(first), len(second)) == (len(STEADY), 5)  # the second read went to the file


class TestShippedManifest:
    def test_the_committed_manifest_loads_and_keeps_the_holdout_out(self) -> None:
        manifest = D1Manifest.load(Path("config/universe/d1/universe.yaml"))

        assert manifest.holdout and manifest.included
        assert not set(manifest.holdout) & set(manifest.included)
        assert not set(manifest.holdout) & set(manifest.audited)
        assert set(manifest.audited) <= set(manifest.included)
        assert not {i for i, _ in manifest.quarantined} & set(manifest.holdout)

    def test_the_committed_holdout_is_what_the_committed_seed_picks(self) -> None:
        manifest = D1Manifest.load(Path("config/universe/d1/universe.yaml"))
        table = Path("config/universe/d1/tokens.csv").read_text().splitlines()[1:]
        candidates = [f"NSE:{line.split(',')[1]}" for line in table]

        picked = SeededHoldout(manifest.seed, manifest.holdout_fraction).pick(
            i for i in candidates if i not in manifest.audited
        )

        assert picked == frozenset(manifest.holdout)
