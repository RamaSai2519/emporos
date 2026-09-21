"""EM-131: what a backtest worker is built from can cross a process boundary, and the engine it
builds reads candles from files only (it has no database to reach)."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from emporos.backtest.engine import BacktestEngine
from emporos.backtest.universe import InstrumentEra
from emporos.cli.backtest_parallel import CurationRecipe, curation_batch
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.risk.config import RiskLimitsLoader

WHEN = datetime(2025, 9, 22, tzinfo=UTC)


def recipe(tmp_path: Path) -> CurationRecipe:
    instrument = Instrument(
        Exchange.NSE, "3045", "SBIN-EQ", "State Bank of India", 1, Money(Decimal("0.05"))
    )
    return CurationRecipe(
        cache_root=tmp_path / "cache",
        snapshot_root=tmp_path / "snapshot",
        eras=(InstrumentEra(instrument, WHEN, None),),
        universe_at=WHEN,
        assume_current_universe=True,
        assume_fees=True,
        limits=RiskLimitsLoader().load(),
    )


def test_the_recipe_survives_a_trip_to_another_process(tmp_path: Path) -> None:
    original = recipe(tmp_path)

    assert pickle.loads(pickle.dumps(original)) == original


def test_it_builds_the_real_engine_over_the_files_alone(tmp_path: Path) -> None:
    built = recipe(tmp_path).build()

    assert isinstance(built, BacktestEngine)  # nothing to connect to, so nothing that can stall


def test_one_worker_means_no_pool_and_the_serial_reference(tmp_path: Path) -> None:
    def make(snapshot: Path) -> CurationRecipe:
        raise AssertionError("no recipe is needed when nothing runs in another process")

    with curation_batch(1, None, tmp_path, make) as batch:  # type: ignore[arg-type]
        assert batch is None
