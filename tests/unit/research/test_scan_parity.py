"""EM-191 F3b: every parity-proven scan against the REAL engine, on the committed year of bars.

The plan's F3 bar (§4.1): screener net expectancy per trade within 10% relative of `backtest
curate`'s, or within 0.02% absolute where that is near zero. This test is stricter as well: the two
must trade the SAME round trips (instrument, day, side). It iterates `PARITY_PROVEN`, so a scan
cannot be listed as proven without passing here.

Scope, stated so the pass is not over-read: two liquid names (RELIANCE, TCS), one year, no risk gate
(as `BacktestJob` runs), one instrument at a time. Cross-instrument limits and thin-volume partial
fills are NOT covered; that is why a screen is triage and a verdict comes from `curate`."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from tests.support.backtest_real import FixtureCandleReader
from tests.support.scan_parity import bars_of, engine_run, execution_of
from tests.unit.research.test_screen import benchmark, evaluator, schedules

from emporos.backtest.engine import BacktestResult
from emporos.backtest.robustness.cost_sensitivity import CostSensitivity
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.proven import PARITY_PROVEN
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_evaluator import ScreenEvaluator, ScreenResult
from emporos.research.screen_trades import DeclaredValueSizer, ScreenTrade

TICK = Decimal("0.1")  # the fixture instruments' tick size
PLAN_STRATEGIES = {"orb_v1", "vwap_reversion_v1", "rsi_pullback_v1"}


@dataclass(frozen=True)
class Pair:
    engine: BacktestResult
    execution: ScanExecution
    bars: dict[str, list[Candle]]


@pytest.fixture(scope="module")
def reader() -> FixtureCandleReader:
    return FixtureCandleReader()


@pytest.fixture(scope="module")
def pairs(reader: FixtureCandleReader) -> Iterator[dict[str, Pair]]:
    async def build() -> dict[str, Pair]:
        out: dict[str, Pair] = {}
        for name in PARITY_PROVEN:
            result = await engine_run(name, reader)
            config = result.spec.config
            bars = {i: await bars_of(reader, i) for i in config.instrument_ids}
            out[name] = Pair(result, execution_of(config, TICK), bars)
        return out

    yield asyncio.run(build())


def scan_trades(name: str, pair: Pair) -> list[ScreenTrade]:
    scan = PARITY_PROVEN[name](pair.engine.spec.config.parameters, pair.execution)
    return [t for i, bars in pair.bars.items() for t in scan.scan(i, bars)]


def engine_round_trips(pair: Pair) -> Counter[tuple[str, str, str]]:
    return Counter(
        (
            t.instrument_id,
            t.opened_at.astimezone(IST).date().isoformat(),
            "BUY" if t.direction.value == "LONG" else "SELL",
        )
        for t in pair.engine.trades
    )


def scan_round_trips(trades: list[ScreenTrade]) -> Counter[tuple[str, str, str]]:
    return Counter((t.instrument_id, t.day.isoformat(), t.side.value) for t in trades)


def tolerance(reference: Decimal) -> Decimal:
    return max(abs(reference) * Decimal("0.10"), Decimal("0.0002"))


def test_the_plan_names_exactly_these_three_strategies() -> None:
    assert set(PARITY_PROVEN) == PLAN_STRATEGIES


@pytest.mark.parametrize("name", sorted(PARITY_PROVEN))
def test_the_scan_takes_the_same_round_trips_as_the_engine(
    name: str, pairs: dict[str, Pair]
) -> None:
    pair = pairs[name]

    engine, scan = engine_round_trips(pair), scan_round_trips(scan_trades(name, pair))

    assert sum(engine.values()) >= 100  # a real sample, not a vacuous match
    assert scan == engine


@pytest.mark.parametrize("name", sorted(PARITY_PROVEN))
def test_the_screener_net_expectancy_is_within_the_plans_bar_of_curate(
    name: str, pairs: dict[str, Pair]
) -> None:
    pair = pairs[name]
    curate = pair.engine.metrics.trades.expectancy
    assert curate is not None

    screened = evaluator().evaluate(
        scan_trades(name, pair), DeclaredValueSizer(pair.execution.position_value), advisory=False
    )

    assert screened.net_mean is not None
    assert abs(screened.net_mean - curate) <= tolerance(curate)


@pytest.mark.parametrize("name", sorted(PARITY_PROVEN))
def test_the_adverse_scenario_agrees_with_curates_own_repricing(
    name: str, pairs: dict[str, Pair]
) -> None:
    pair = pairs[name]
    config = benchmark()
    (adverse,) = CostSensitivity().evaluate(
        list(pair.engine.trades), [config.scenario(config.adverse_scenario)]
    )
    notional = sum((t.entry_notional.amount for t in pair.engine.trades), Decimal(0))
    curate_adverse = adverse.net_pnl / notional
    scenario = ScreenCostScenario.from_benchmark(config, config.adverse_scenario)

    screened: ScreenResult = ScreenEvaluator(
        ScreenCostModel(schedules()), scenario, scenario
    ).evaluate(
        scan_trades(name, pair), DeclaredValueSizer(pair.execution.position_value), advisory=False
    )

    assert screened.net_mean is not None
    assert abs(screened.net_mean - curate_adverse) <= tolerance(curate_adverse)


def test_the_check_has_teeth_a_scan_with_different_rules_does_not_match(
    pairs: dict[str, Pair],
) -> None:
    """Negative control: the same engine run against orb_v1's scan with shorting switched off must
    disagree, so a passing result above is evidence and not an artefact of a loose comparison."""
    pair = pairs["orb_v1"]
    parameters = pair.engine.spec.config.parameters.model_copy(update={"allow_short": False})
    scan = PARITY_PROVEN["orb_v1"](parameters, pair.execution)
    trades = [t for i, bars in pair.bars.items() for t in scan.scan(i, bars)]

    assert scan_round_trips(trades) != engine_round_trips(pair)
