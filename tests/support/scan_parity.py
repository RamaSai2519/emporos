"""F3b parity rig: one strategy through the REAL engine and through its signal scan, on the same
committed bars, parameters and declared size. No database, no network.

The engine side is `BacktestJob` over `FixtureCandleReader` (as `real_year_backtest`), with the
strategy's shipped YAML and its universe swapped for the two fixture instruments. The scan side
reads the very same bars and takes every number (parameters, size, buffer, session times) from the
engine's own resolved config, so the only thing that can differ is the trading logic."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import yaml

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.engine import BacktestResult
from emporos.backtest.job import BacktestJob, BacktestRequest
from emporos.cli.strategy_composition import build_registry
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import InstrumentResolver
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.scans.base import ScanExecution
from emporos.research.screen_trades import ScreenTrade
from emporos.session.strategy_files import STRATEGY_CONFIG_DIR
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver
from tests.support.backtest_real import (
    FIRST_DAY,
    LAST_DAY,
    STARTING_CASH,
    FixtureCandleReader,
    fixture_eras,
)

FIXTURE_UNIVERSE = ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"]


def _config_for(strategy: str, resolver: InstrumentResolver) -> ResolvedStrategyConfig:
    raw: dict[str, Any] = yaml.safe_load((STRATEGY_CONFIG_DIR / f"{strategy}.yaml").read_text())
    raw["universe"] = {"type": "static", "instruments": FIXTURE_UNIVERSE}
    return StrategyConfigResolver(build_registry(), resolver).resolve(
        raw, source=f"{strategy}.yaml"
    )


async def engine_run(strategy: str, reader: FixtureCandleReader | None = None) -> BacktestResult:
    library = FeeScheduleLibrary.from_directory()
    job = BacktestJob(
        reader or FixtureCandleReader(),
        build_registry(),
        fixture_eras(),
        lambda resolver: _config_for(strategy, resolver),
        lambda: EarliestBeforeFirst(library),
    )
    return await job.run(
        BacktestRequest(FIRST_DAY, LAST_DAY, STARTING_CASH, assume_current_universe=True)
    )


def execution_of(config: ResolvedStrategyConfig, tick_size: Decimal) -> ScanExecution:
    """The scan's assumptions, read from the config the engine ran under."""
    return ScanExecution(
        position_value=config.risk.max_position_value,
        limit_buffer_bps=config.execution.limit_buffer_bps,
        tick_size=tick_size,
        no_new_entries_after=config.session.no_new_entries_after,
        square_off_at=config.session.square_off_at,
    )


async def bars_of(reader: FixtureCandleReader, instrument_id: str) -> list[Candle]:
    return await reader.get_range(
        instrument_id,
        Timeframe.M5,
        datetime(2000, 1, 1, tzinfo=UTC),
        datetime(2100, 1, 1, tzinfo=UTC),
    )


def described(trades: list[ScreenTrade]) -> list[tuple[str, str, str]]:
    return sorted((t.instrument_id, t.day.isoformat(), t.side.value) for t in trades)
