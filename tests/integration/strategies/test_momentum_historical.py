"""EM-69 acceptance: the reference strategy emits signals from HISTORICAL bars, deterministically.

The bars are written to the real Atlas hot tier under scratch instrument ids (never a real `NSE:*`
row) and read back through `CandleRepository`, exactly as a backtest will. Only our own rows are
deleted afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any

import pytest
import yaml
from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.feed import ClosedBarFeed, FeedWindow
from emporos.cli.strategy_composition import build_registry
from emporos.core.config import CONFIG_DIR
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.domain.signals import SignalKind
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.store import InstrumentRecordMapper
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.repositories import InstrumentRepository
from emporos.strategies.resolution import StrategyConfigResolver
from tests.support.fakes import InMemoryObjectStore
from tests.support.strategies import (
    T0,
    replay,
    session_bars,
    wave_closes,
)

pytestmark = pytest.mark.integration

DAYS = 5
PER_DAY = 75


class Scratch:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.instruments = [
            Instrument(
                Exchange.NSE, f"zz{suffix}{n}", f"ZZ{suffix}{n}-EQ", "Scratch", 1, Money.of("0.05")
            )
            for n in "ab"
        ]
        self.ids = [i.instrument_id for i in self.instruments]
        self.hot = MongoCandleStore(database)
        self.repository = CandleRepository(self.hot, ParquetCandleArchive(InMemoryObjectStore()))
        self.bars: dict[str, list[Candle]] = {}

    def config(self):  # type: ignore[no-untyped-def]
        raw = yaml.safe_load((CONFIG_DIR / "strategies" / "momentum_v1.yaml").read_text())
        raw["universe"]["instruments"] = [f"NSE:{i.tradingsymbol}" for i in self.instruments]
        master = InstrumentCache(self.instruments)
        return StrategyConfigResolver(build_registry(), master).resolve(raw)

    async def load(self) -> None:
        """Two instruments on offset waves, so their crossovers do not coincide."""
        for shift, instrument_id in enumerate(self.ids):
            closes = wave_closes(DAYS * PER_DAY + 40 * shift, period=90, amplitude=60)[40 * shift :]
            self.bars[instrument_id] = session_bars(closes, instrument_id)
            await self.hot.upsert(self.bars[instrument_id])

    def feed(self) -> ClosedBarFeed:
        return ClosedBarFeed(
            self.repository,
            self.ids,
            Timeframe.M5,
            FeedWindow(T0 - timedelta(days=1), T0 + timedelta(days=DAYS + 1)),
        )

    async def cleanup(self) -> None:
        for instrument_id, bars in self.bars.items():
            await self.hot.delete(instrument_id, Timeframe.M5, [b.ts for b in bars])


@pytest.fixture
async def scratch(database: AsyncDatabase[Mapping[str, Any]]) -> AsyncIterator[Scratch]:
    scratch = Scratch(database)
    try:
        await scratch.load()
        yield scratch
    finally:
        await scratch.cleanup()


async def test_the_reference_strategy_emits_signals_from_historical_bars(scratch: Scratch) -> None:
    result = await replay(scratch.config(), [b async for b in scratch.feed()], fills=True)

    assert not result.report.halted and result.alerts == []
    kinds = {s.kind for s in result.signals}
    assert kinds == {SignalKind.ENTRY, SignalKind.EXIT}  # it both opens and closes exposure
    assert {s.instrument_id for s in result.signals} == set(scratch.ids)
    # every signal is priced off a bar that really is in the repository, at that bar's close
    closes = {
        (b.instrument_id, b.closes_at): b.close for bars in scratch.bars.values() for b in bars
    }
    for signal in result.signals:
        assert closes[(signal.instrument_id, signal.ts)] == signal.limit_price


async def test_running_it_again_gives_exactly_the_same_signals(scratch: Scratch) -> None:
    first = await replay(scratch.config(), [b async for b in scratch.feed()], fills=True)
    second = await replay(scratch.config(), [b async for b in scratch.feed()], fills=True)

    assert first.signals and first.signals == second.signals


async def test_bars_read_back_from_atlas_give_the_same_signals_as_bars_in_memory(
    scratch: Scratch,
) -> None:
    from_atlas = await replay(scratch.config(), [b async for b in scratch.feed()], fills=True)
    in_memory = sorted(
        (b for bars in scratch.bars.values() for b in bars),
        key=lambda b: (b.ts, scratch.ids.index(b.instrument_id)),
    )

    assert from_atlas.signals == (await replay(scratch.config(), in_memory, fills=True)).signals


async def test_the_shipped_config_resolves_against_the_real_instrument_master(
    database: AsyncDatabase[Mapping[str, Any]],
) -> None:
    """A read-only check that `config/strategies/momentum_v1.yaml` names real instruments."""
    repository = InstrumentRepository(database)
    mapper = InstrumentRecordMapper()
    records = [
        await repository.get_by_symbol("NSE", symbol) for symbol in ("RELIANCE-EQ", "TCS-EQ")
    ]
    if not all(records):
        pytest.skip("the instrument master has not been synced into emporos_dev")
    master = InstrumentCache([mapper.to_domain(r) for r in records if r])

    raw = yaml.safe_load((CONFIG_DIR / "strategies" / "momentum_v1.yaml").read_text())
    config = StrategyConfigResolver(build_registry(), master).resolve(raw)

    assert [m.symbol for m in config.universe] == ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"]
    assert all(m.instrument_id.startswith("NSE:") for m in config.universe)
