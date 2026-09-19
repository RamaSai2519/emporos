"""EM-70: the strategy determinism suite (plan.md §9, §22 Phase 9).

Identical input must give identical signals — not merely "twice in one process". So the same
replay is run in fresh interpreters under different hash seeds and time zones, under a frozen and
a different wall clock, under a hostile ambient Decimal context, and after the global RNG has been
re-seeded. A strategy that leaned on any of those would give a different answer somewhere here.

All inputs are fixture series: no network, no database, no broker.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from decimal import ROUND_DOWN, Context, localcontext
from pathlib import Path
from typing import Any

import yaml
from freezegun import freeze_time

from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.signals import Signal
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.session.strategy_runs import StrategyRunLauncher
from emporos.strategies.resolution import StrategyConfigResolver
from tests.support.strategies import (
    INSTRUMENT_MASTER,
    T0,
    InMemoryRunStore,
    InMemoryStrategyStore,
    changed,
    closes_to_bars,
    momentum_raw,
    replay,
    resolved,
    wave_closes,
)

REPO = Path(__file__).resolve().parents[3]
SERIES = wave_closes(400, period=40, amplitude=30)


def _rendered(signals: list[Signal]) -> list[list[str]]:
    return [
        [s.kind.value, s.side.value, s.order_type.value, str(s.quantity), str(s.limit_price.amount),
         s.ts.isoformat(), s.instrument_id, s.reason]
        for s in signals
    ]  # fmt: skip


async def _run(raw: dict[str, Any] | None = None, closes: list[str] = SERIES) -> list[Signal]:
    result = await replay(resolved(raw or momentum_raw()), closes_to_bars(closes), fills=True)
    assert not result.report.halted
    return result.signals


async def test_the_fixture_series_actually_trades() -> None:
    """Guards every test below against passing vacuously on an empty signal list."""
    signals = await _run()
    assert len(signals) >= 8 and {s.kind.value for s in signals} == {"ENTRY", "EXIT"}


async def test_identical_input_gives_identical_signals_in_one_process() -> None:
    first, second, third = await _run(), await _run(), await _run()
    assert first == second == third


async def test_the_wall_clock_is_never_consulted() -> None:
    with freeze_time("2020-03-01 10:00:00"):
        early = await _run()
    with freeze_time("2035-11-30 23:59:59"):
        late = await _run()

    assert early == late == await _run()


async def test_the_global_random_state_is_never_consulted() -> None:
    random.seed(1)
    first = await _run()
    random.seed(999)
    second = await _run()
    assert first == second


async def test_a_hostile_ambient_decimal_context_changes_nothing() -> None:
    baseline = await _run()
    with localcontext(Context(prec=4, rounding=ROUND_DOWN)):
        squeezed = await _run()
    assert squeezed == baseline


async def test_separate_runs_share_no_state() -> None:
    """A strategy's state lives in the instance: running a different series in between leaves the
    next run of the first series untouched."""
    baseline = await _run()
    await _run(closes=wave_closes(200, period=25, amplitude=15))
    assert await _run() == baseline


def _replay_in_fresh_interpreter(hash_seed: str, tz: str) -> str:
    code = (
        "import asyncio, json\n"
        "from tests.support.strategies import closes_to_bars, momentum_raw, replay, resolved, "
        "wave_closes\n"
        "async def main():\n"
        "    result = await replay(resolved(momentum_raw()), "
        "closes_to_bars(wave_closes(400, period=40, amplitude=30)), fills=True)\n"
        "    print(json.dumps([[s.kind.value, s.quantity, str(s.limit_price.amount), "
        "s.ts.isoformat(), s.reason] for s in result.signals]))\n"
        "asyncio.run(main())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "TZ": tz, "PYTHONPATH": str(REPO)}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
        check=True, timeout=120,
    )  # fmt: skip
    return done.stdout.strip()


def test_fresh_interpreters_agree_whatever_the_hash_seed_or_time_zone() -> None:
    outputs = {
        (seed, tz): _replay_in_fresh_interpreter(seed, tz)
        for seed, tz in [
            ("0", "UTC"),
            ("1", "Asia/Kolkata"),
            ("4242", "America/New_York"),
            ("random", "Pacific/Auckland"),
        ]
    }

    assert len(set(outputs.values())) == 1
    assert len(json.loads(next(iter(outputs.values())))) >= 8


BLOCKED_IMPORTS = [
    "emporos.broker",
    "emporos.persistence",
    "emporos.execution",
    "emporos.marketdata",
    "pymongo",
    "httpx",
    "boto3",
    "yaml",
    "websockets",
]

ISOLATED_REPLAY = """
import sys
for name in {blocked!r}:
    sys.modules[name] = None  # any import of these now raises ImportError

import asyncio, logging
from datetime import UTC, datetime, timedelta
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.strategies.builtin.momentum_v1 import MomentumV1
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from emporos.strategies.positions import FlatPositions
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.runner import ReplayClockSync, StrategyRunner

class Master:
    def by_symbol(self, exchange, symbol):
        return Instrument(exchange, "1001", symbol, "x", 1, Money.of("0.05"))

class Sink:
    def __init__(self): self.signals = []
    async def submit(self, signal): self.signals.append(signal)

registry = StrategyRegistry(); registry.register(MomentumV1)
raw = {raw!r}
config = StrategyConfigResolver(registry, Master()).resolve(raw)
start = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)
clock = FixedClock(start); history = ClosedBarHistory(clock); sink = Sink()
ctx = StrategyContext("run-1", config, clock, logging.getLogger("x"), history, FlatPositions(),
                      __import__("random").Random(0))
runner = StrategyRunner(registry.create(config), ctx, history, sink, ReplayClockSync(clock),
                        LogAlertSink())
closes = {closes!r}
async def feed():
    for i, close in enumerate(closes):
        price = Money.of(close)
        yield Candle("NSE:1001", Timeframe.M5, start + timedelta(minutes=5 * i), price, price,
                     price, price, 10)
async def main():
    report = await runner.run(feed())
    print(len(sink.signals), report.halted)
asyncio.run(main())
"""


def test_a_strategy_runs_with_every_broker_storage_and_network_package_unimportable() -> None:
    """The strongest form of "a strategy cannot reach a broker": take the packages away, and the
    reference strategy — resolver, registry, context, runner and all — does not notice."""
    raw = momentum_raw()
    raw["universe"] = {"type": "static", "instruments": ["NSE:ALPHA-EQ"]}
    code = ISOLATED_REPLAY.format(blocked=BLOCKED_IMPORTS, raw=raw, closes=SERIES)

    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120,
    )  # fmt: skip

    assert done.returncode == 0, done.stderr[-2000:]
    signal_count, halted = done.stdout.split()
    assert int(signal_count) > 0 and halted == "False"


def test_that_isolation_check_really_blocks_the_packages() -> None:
    """Guards the test above against silently blocking nothing."""
    code = (
        "import sys\n"
        "sys.modules['emporos.broker'] = None\n"
        "try:\n"
        "    import emporos.broker.base\n"
        "except ImportError:\n"
        "    print('blocked')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert done.stdout.strip() == "blocked"


def _write_yaml(directory: Path, edits: dict[str, object] | None = None) -> Path:
    raw = yaml.safe_load((REPO / "config" / "strategies" / "momentum_v1.yaml").read_text())
    raw["universe"]["instruments"] = ["NSE:ALPHA-EQ", "NSE:BETA-EQ"]
    for path, value in (edits or {}).items():
        raw = changed(raw, path, value)
    path = directory / "momentum_v1.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


async def test_a_config_snapshot_reproduces_a_run_exactly_after_the_yaml_changes(
    tmp_path: Path,
) -> None:
    registry = build_registry()
    loader = StrategyConfigLoader(StrategyConfigResolver(registry, INSTRUMENT_MASTER), tmp_path)
    launcher = StrategyRunLauncher(
        registry, InMemoryStrategyStore(), InMemoryRunStore(), FixedClock(T0), IdGenerator()
    )
    bars = closes_to_bars(SERIES)

    _write_yaml(
        tmp_path, {"parameters.fast_ema": 3, "parameters.slow_ema": 8, "parameters.rsi_period": 4}
    )
    original = await launcher.start(loader.load_all()[0], "2026-01-05")
    baseline = await replay(original.config, bars, fills=True, run_id=original.run_id)
    assert baseline.signals

    # The YAML on disk is edited (different periods): a fresh start from it behaves differently...
    _write_yaml(
        tmp_path, {"parameters.fast_ema": 5, "parameters.slow_ema": 13, "parameters.rsi_period": 6}
    )
    edited = await launcher.start(loader.load_all()[0], "2026-01-06")
    assert (await replay(edited.config, bars, fills=True, run_id=edited.run_id)).signals != (
        baseline.signals
    )

    # ...yet the recorded run reproduces from its snapshot, signal for signal.
    reproduced = await launcher.load(original.run_id)
    again = await replay(reproduced.config, bars, fills=True, run_id=reproduced.run_id)
    assert again.signals == baseline.signals
