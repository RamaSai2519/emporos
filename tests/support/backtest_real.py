"""The acceptance run on the committed fixture: a year of real 5m bars (RELIANCE, TCS) through the
real `BacktestJob`, the real shipped `momentum_v1.yaml` and the real dated fee schedule.

Nothing here touches a database or a network: the bars are frozen in `tests/fixtures/backtest/`,
pinned by the manifest's SHA-256. `FixtureCandleReader` implements the same `CandleReader`
Protocol as `CandleRepository`."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from bisect import bisect_left
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.engine import BacktestResult
from emporos.backtest.job import BacktestJob, BacktestRequest
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.cli.strategy_composition import build_registry
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument, InstrumentResolver
from emporos.domain.money import Money
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.session.strategy_files import STRATEGY_CONFIG_DIR, StrategyConfigLoader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backtest"
FIRST_DAY, LAST_DAY = date(2025, 9, 22), date(2026, 9, 18)
STARTING_CASH = Money.of("100000")


def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text())
    return loaded


def bars_bytes() -> bytes:
    return (FIXTURES / manifest()["file"]).read_bytes()


class FixtureCandleReader:
    """`CandleReader` over the committed CSV. Read once, served in time order by binary search."""

    def __init__(self, data: bytes | None = None) -> None:
        self._series: dict[tuple[str, Timeframe], list[Candle]] = {}
        rows = csv.DictReader(io.StringIO(gzip.decompress(data or bars_bytes()).decode()))
        for row in rows:
            bar = Candle(
                instrument_id=row["instrument_id"],
                timeframe=Timeframe.M5,
                ts=datetime.fromisoformat(row["ts"]).astimezone(UTC),
                open=Money(Decimal(row["open"])),
                high=Money(Decimal(row["high"])),
                low=Money(Decimal(row["low"])),
                close=Money(Decimal(row["close"])),
                volume=int(row["volume"]),
                partial=row["partial"] == "1",
            )
            self._series.setdefault((bar.instrument_id, bar.timeframe), []).append(bar)

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        series = self._series.get((instrument_id, timeframe), [])
        lo = bisect_left(series, start, key=lambda bar: bar.ts)
        hi = bisect_left(series, end, key=lambda bar: bar.ts)
        return series[lo:hi]

    @property
    def total_bars(self) -> int:
        return sum(len(series) for series in self._series.values())


def fixture_eras() -> AsOfInstruments:
    """The two instruments as the master held them, current since their recorded `valid_from`."""
    raw = json.loads((FIXTURES / "instruments.json").read_text())
    return AsOfInstruments(
        [
            InstrumentEra(
                Instrument(
                    Exchange(r["exchange"]), r["token"], r["tradingsymbol"], r["name"],
                    r["lot_size"], Money(Decimal(r["tick_size"])),
                ),
                datetime.fromisoformat(r["valid_from"]),
                None,
            )
            for r in raw
        ]
    )  # fmt: skip


async def real_year_backtest(request: BacktestRequest | None = None) -> BacktestResult:
    """The acceptance run (the whole year), or the same wiring over another window."""
    registry = build_registry()
    library = FeeScheduleLibrary.from_directory()

    def config_for(resolver: InstrumentResolver) -> ResolvedStrategyConfig:
        return StrategyConfigLoader(StrategyConfigResolver(registry, resolver)).load_file(
            STRATEGY_CONFIG_DIR / "momentum_v1.yaml"
        )

    job = BacktestJob(
        FixtureCandleReader(),
        registry,
        fixture_eras(),
        config_for,
        lambda: EarliestBeforeFirst(library),
    )
    return await job.run(
        request or BacktestRequest(FIRST_DAY, LAST_DAY, STARTING_CASH, assume_current_universe=True)
    )


def sha256_of_bars() -> str:
    return hashlib.sha256(bars_bytes()).hexdigest()
