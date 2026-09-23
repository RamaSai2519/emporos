"""EM-185: the shadow backtest reproduces a paper run's config, or refuses."""

from __future__ import annotations

from datetime import date

import pytest

from emporos.backtest.engine import BacktestEngine
from emporos.backtest.journal import BacktestEventSink
from emporos.domain.money import Money
from emporos.parity.errors import ConfigDrift
from emporos.parity.shadow import ShadowBacktest
from emporos.persistence.records import StrategyRunRecord
from emporos.strategies.snapshot import ConfigSnapshotter
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import (
    WORKED_DAY,
    BuyThenSell,
    FixedSchedule,
    FixedTicks,
    bars,
    config,
    registry,
)
from tests.support.strategies import T0


class Engines:
    def __init__(self) -> None:
        self.built_for: list[date] = []
        self._reader = InMemoryCandles(bars(WORKED_DAY))

    def build(self, session_date: date, sink: BacktestEventSink) -> BacktestEngine:
        self.built_for.append(session_date)
        return BacktestEngine(
            self._reader, registry(), FixedTicks(), lambda: FixedSchedule(), sink=sink
        )


def paper_run(**overrides: object) -> StrategyRunRecord:
    snapshot = ConfigSnapshotter().take(config(buy_at=2, sell_at=5))
    fields: dict[str, object] = {
        "_id": "run-1",
        "strategy_id": "s",
        "session_date": T0.date().isoformat(),
        "created_at": T0,
        "config_snapshot": snapshot.document,
        "config_hash": snapshot.content_hash,
    }
    fields.update(overrides)
    return StrategyRunRecord.model_validate(fields)


def shadow(engines: Engines | None = None) -> tuple[ShadowBacktest, Engines]:
    built = engines or Engines()
    return ShadowBacktest(built, registry(), Money.of("100000")), built


async def test_it_replays_the_recorded_config_over_the_session_and_journals_it() -> None:
    BuyThenSell.reset()
    backtest, engines = shadow()

    result = await backtest.run(paper_run())

    assert engines.built_for == [T0.date()]
    assert result.config == config(buy_at=2, sell_at=5)
    assert [s.signal.side.value for s in result.journal.signals] == ["BUY", "SELL"]
    assert len(result.result.trades) == 1
    assert result.result.spec.window.start < T0 < result.result.spec.window.end


async def test_a_run_with_no_recorded_hash_is_refused() -> None:
    with pytest.raises(ConfigDrift, match="no config hash"):
        await shadow()[0].run(paper_run(config_hash=None))


async def test_a_snapshot_that_no_longer_matches_its_hash_is_refused() -> None:
    tampered = dict(paper_run().config_snapshot)
    tampered["execution"] = {**tampered["execution"], "max_reprices": 99}  # type: ignore[dict-item]
    with pytest.raises(ConfigDrift):
        await shadow()[0].run(paper_run(config_snapshot=tampered))


async def test_a_snapshot_of_an_unknown_strategy_is_refused() -> None:
    document = {**paper_run().config_snapshot, "name": "not_a_strategy"}
    hash_ = ConfigSnapshotter.hash_of(document)  # type: ignore[arg-type]
    with pytest.raises(ConfigDrift):
        await shadow()[0].run(paper_run(config_snapshot=document, config_hash=hash_))
