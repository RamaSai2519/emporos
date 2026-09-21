from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.signals import Signal
from emporos.persistence.records import StrategyRecord
from emporos.session.strategy_runs import (
    PreparedRun,
    RunEnvironment,
    StartedRun,
    StrategyRunLauncher,
    StrategyRunnerBuilder,
    UnknownRunError,
)
from emporos.strategies.positions import FlatPositions
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.runner import ReplayClockSync
from emporos.strategies.snapshot import SnapshotIntegrityError
from tests.support.fakes import RecordingAlertSink
from tests.support.strategies import (
    INSTRUMENT_MASTER,
    T0,
    InMemoryRunStore,
    InMemoryStrategyStore,
    ListFeed,
    ListSignalSink,
    bar_at,
    changed,
    raw_config,
    threshold_registry,
)

NOW = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)


def _config(raw: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    return StrategyConfigResolver(threshold_registry(), INSTRUMENT_MASTER).resolve(
        raw or raw_config()
    )


class Rig:
    def __init__(self, race_winner: StrategyRecord | None = None) -> None:
        self.strategies = InMemoryStrategyStore(race_winner)
        self.runs = InMemoryRunStore()
        self.launcher = StrategyRunLauncher(
            threshold_registry(), self.strategies, self.runs, FixedClock(NOW), IdGenerator()
        )
        self.builder = StrategyRunnerBuilder(threshold_registry())

    def build(self, started: StartedRun, sink: ListSignalSink | None = None) -> PreparedRun:
        clock = FixedClock(T0)
        environment = RunEnvironment(
            clock,
            ReplayClockSync(clock),
            sink or ListSignalSink(),
            FlatPositions(),
            RecordingAlertSink(),
        )
        return self.builder.build(started, environment)

    async def replay(self, started: StartedRun, bars: list[Any]) -> list[Signal]:
        sink = ListSignalSink()
        await self.build(started, sink).runner.run(ListFeed(bars))
        return sink.signals


BARS = [
    bar_at(minutes=0, close="99"),
    bar_at(minutes=5, close="105"),
    bar_at(minutes=10, close="120"),
]


async def test_starting_a_run_records_the_frozen_config_and_its_hash() -> None:
    rig = Rig()

    started = await rig.launcher.start(_config(), "2026-01-05")

    (record,) = rig.runs.records.values()
    assert record.id == started.run_id and record.session_date == "2026-01-05"
    assert record.created_at == NOW and record.strategy_name == "threshold"
    assert record.config_snapshot == started.snapshot.document
    assert record.config_hash == started.snapshot.content_hash
    assert json.loads(json.dumps(record.config_snapshot)) == record.config_snapshot


async def test_the_run_record_is_written_before_the_runner_exists() -> None:
    """`start` returns only after the snapshot is stored; nothing has seen market data yet."""
    rig = Rig()
    started = await rig.launcher.start(_config(), "2026-01-05")
    assert started.run_id in rig.runs.records


async def test_the_strategy_catalogue_entry_is_created_once_and_keeps_the_latest_config() -> None:
    rig = Rig()
    first = await rig.launcher.start(_config(), "2026-01-05")
    second = await rig.launcher.start(
        _config(changed(raw_config(), "parameters.threshold", "1")), "2026-01-06"
    )

    (strategy,) = rig.strategies.records.values()
    assert strategy.name == "threshold"
    assert strategy.config == second.snapshot.document != first.snapshot.document
    assert {r.strategy_id for r in rig.runs.records.values()} == {strategy.id}
    assert len(rig.runs.records) == 2


async def test_losing_the_race_to_register_a_strategy_adopts_the_winners_entry() -> None:
    winner = StrategyRecord(_id="winner-id", name="threshold", config={})
    rig = Rig(race_winner=winner)

    await rig.launcher.start(_config(), "2026-01-05")

    (run,) = rig.runs.records.values()
    assert run.strategy_id == "winner-id"


async def test_a_run_reproduces_from_its_snapshot_after_the_yaml_changes() -> None:
    """EM-67 acceptance: the snapshot, not the file, defines the run."""
    rig = Rig()
    original = await rig.launcher.start(_config(), "2026-01-05")  # threshold 100
    original_signals = await rig.replay(original, BARS)
    assert [s.limit_price.amount for s in original_signals] == [105, 120]

    # The YAML is edited afterwards: a new run from it behaves differently...
    edited = await rig.launcher.start(
        _config(changed(raw_config(), "parameters.threshold", "110")), "2026-01-06"
    )
    assert [s.limit_price.amount for s in await rig.replay(edited, BARS)] == [120]

    # ...but the first run, loaded by id, reproduces exactly what it did.
    reproduced = await rig.launcher.load(original.run_id)
    assert reproduced.config == original.config
    assert await rig.replay(reproduced, BARS) == original_signals


async def test_loading_an_unknown_run_fails_clearly() -> None:
    with pytest.raises(UnknownRunError, match="nope"):
        await Rig().launcher.load("nope")


async def test_a_run_whose_stored_snapshot_was_altered_is_not_reproduced() -> None:
    rig = Rig()
    started = await rig.launcher.start(_config(), "2026-01-05")
    record = rig.runs.records[started.run_id]
    altered = json.loads(json.dumps(record.config_snapshot))
    altered["parameters"]["threshold"] = "1"
    rig.runs.records[started.run_id] = record.model_copy(update={"config_snapshot": altered})

    with pytest.raises(SnapshotIntegrityError):
        await rig.launcher.load(started.run_id)


async def test_the_builder_wires_a_runner_that_never_sees_a_broker() -> None:
    rig = Rig()
    started = await rig.launcher.start(_config(), "2026-01-05")

    prepared = rig.build(started)

    assert prepared.run_id == started.run_id and prepared.context.run_id == started.run_id
    assert prepared.context.config == started.config
    assert prepared.runner.state.value == "NEW"


async def test_the_rng_is_seeded_from_the_config_so_a_reproduced_run_draws_the_same_numbers() -> (
    None
):
    rig = Rig()
    first = await rig.launcher.start(_config(), "2026-01-05")
    again = await rig.launcher.load(first.run_id)
    other_raw = changed(raw_config(), "risk.max_open_positions", 4)
    other = await rig.launcher.start(_config(other_raw), "2026-01-05")

    def draws(started: StartedRun) -> list[float]:
        rng = rig.build(started).context.rng
        return [rng.random() for _ in range(3)]

    assert draws(first) == draws(again)
    assert draws(first) != draws(other)


async def test_a_strategy_can_be_registered_without_ever_running() -> None:
    """The dashboard lists the catalogue: a loadable strategy is in it before its first run."""
    rig = Rig()

    strategy_id = await rig.launcher.register(_config())

    (strategy,) = rig.strategies.records.values()
    assert strategy.id == strategy_id and strategy.name == "threshold"
    assert not rig.runs.records  # registered, not started


async def test_the_catalogue_entry_carries_the_behaviour_hash_and_ignores_enabled() -> None:
    rig = Rig()
    await rig.launcher.register(_config(changed(raw_config(), "enabled", False)))
    (before,) = rig.strategies.records.values()

    await rig.launcher.register(_config(changed(raw_config(), "enabled", True)))
    (after,) = rig.strategies.records.values()

    assert before.behaviour_hash is not None
    assert after.behaviour_hash == before.behaviour_hash  # switching on keeps the verdict valid
    assert after.id == before.id
