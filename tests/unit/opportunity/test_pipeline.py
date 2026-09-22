from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.opportunity.allocator import AllocationConstraints, PortfolioAllocator
from emporos.opportunity.pipeline import OpportunityPipeline, StrategyRun
from emporos.opportunity.scanner import OpportunityScanner
from emporos.risk.snapshot import AccountFacts
from emporos.strategies.config import RiskSettings
from emporos.strategies.context import StrategyContext
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from tests.support.strategies import (
    INSTRUMENT,
    OTHER_INSTRUMENT,
    T0,
    ScriptedStrategy,
    bar_at,
    make_config,
    make_context,
    make_signal,
)


class _FixedRegime:
    """A `RegimeSource` double that skips the real classifier's warm-up entirely."""

    def __init__(self, regime: MarketRegime | None) -> None:
        self._regime = regime

    def update(self, candle: Candle) -> MarketRegime | None:
        return self._regime


def _risk_settings() -> RiskSettings:
    return RiskSettings(
        max_position_value=Decimal(50000),
        max_open_positions=3,
        stop_loss_pct=Decimal(2),
        target_pct=Decimal(4),
    )


def _constraints(**overrides: object) -> AllocationConstraints:
    defaults: dict[str, object] = {
        "production_capital": Money.of("50000"),
        "max_simultaneous_positions": 3,
        "max_risk_per_trade": Money.of("1000"),
        "max_portfolio_risk": Money.of("3000"),
    }
    defaults.update(overrides)
    return AllocationConstraints(**defaults)  # type: ignore[arg-type]


def _strategy_run(
    strategy_name: str,
    instrument_id: str,
    signals: Iterable[Signal] = (),
) -> StrategyRun:
    class _Named(ScriptedStrategy):
        name = strategy_name

    config = make_config(name=strategy_name, instruments=(instrument_id,))
    strategy = _Named(config)
    context: StrategyContext = make_context(FixedClock(T0), config=config)
    strategy.initialize(context)
    strategy._outbox = list(signals)
    return StrategyRun(
        strategy_name=strategy_name,
        instrument_id=instrument_id,
        timeframe=Timeframe.M5,
        strategy=strategy,
        risk_settings=_risk_settings(),
    )


def _pipeline(
    runs: list[StrategyRun],
    *,
    regime: MarketRegime | None = MarketRegime.TRENDING,
    account: AccountFacts | None = None,
    constraints: AllocationConstraints | None = None,
) -> OpportunityPipeline:
    account = account if account is not None else AccountFacts()
    registry = StrategyRegistry()
    for run in runs:
        registry.register(type(run.strategy))
    regimes = {
        instrument_id: _FixedRegime(regime) for instrument_id in {r.instrument_id for r in runs}
    }
    return OpportunityPipeline(
        runs=runs,
        regimes=regimes,
        scanner=OpportunityScanner(registry),
        allocator=PortfolioAllocator(),
        account=lambda: account,
        constraints=constraints or _constraints(),
    )


def test_on_bars_rejects_an_empty_batch() -> None:
    pipeline = _pipeline([])
    with pytest.raises(ValueError, match="at least one"):
        pipeline.on_bars([])


def test_a_strategy_with_no_signal_produces_no_outcome() -> None:
    run = _strategy_run("alpha", INSTRUMENT)
    pipeline = _pipeline([run])

    outcome = pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.exits == ()
    assert outcome.allocations == ()


def test_exit_signals_always_pass_through_unranked() -> None:
    exit_signal = make_signal(instrument_id=INSTRUMENT, kind=SignalKind.EXIT)
    run = _strategy_run("alpha", INSTRUMENT, signals=[exit_signal])
    pipeline = _pipeline([run])

    outcome = pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.exits == (exit_signal,)
    assert outcome.allocations == ()


def test_an_entry_signal_with_a_classified_regime_is_allocated() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100", side=OrderSide.BUY)
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = _pipeline([run])

    outcome = pipeline.on_bars([bar_at(INSTRUMENT)])

    assert len(outcome.allocations) == 1
    assert outcome.allocations[0].candidate.strategy_name == "alpha"
    assert outcome.approved_signals[0].instrument_id == INSTRUMENT


def test_an_entry_with_no_classified_regime_is_rejected_not_allocated() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = _pipeline([run], regime=None)

    outcome = pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.allocations == ()
    assert len(outcome.scan.rejected) == 1


def test_no_regime_source_for_an_instrument_is_treated_as_unclassified() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = OpportunityPipeline(
        runs=[run],
        regimes={},
        scanner=OpportunityScanner(_registry_for([run])),
        allocator=PortfolioAllocator(),
        account=lambda: AccountFacts(),
        constraints=_constraints(),
    )

    outcome = pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.allocations == ()


def test_a_bar_for_an_instrument_with_no_strategy_runs_is_a_no_op() -> None:
    run = _strategy_run("alpha", INSTRUMENT)
    pipeline = _pipeline([run])

    outcome = pipeline.on_bars([bar_at(OTHER_INSTRUMENT)])

    assert outcome.exits == ()
    assert outcome.allocations == ()


def test_a_multi_instrument_batch_ranks_across_the_whole_universe_not_per_instrument() -> None:
    """Two instruments close in the same batch and capital only stretches to one share total. If
    the pipeline evaluated each instrument's bar in isolation (calling the allocator once per
    candle with the full budget every time), both would be allocated; a single shared scan+
    allocate call over the whole batch correctly caps it at one."""
    run_a = _strategy_run(
        "alpha", INSTRUMENT, signals=[make_signal(instrument_id=INSTRUMENT, price="100")]
    )
    run_b = _strategy_run(
        "beta", OTHER_INSTRUMENT, signals=[make_signal(instrument_id=OTHER_INSTRUMENT, price="100")]
    )
    pipeline = _pipeline(
        [run_a, run_b],
        constraints=_constraints(
            production_capital=Money.of("100"),  # only enough for one share of one candidate
            max_risk_per_trade=Money.of("10"),  # 2/share risk, so risk never binds here
        ),
    )

    outcome = pipeline.on_bars([bar_at(INSTRUMENT), bar_at(OTHER_INSTRUMENT)])

    assert len(outcome.allocations) == 1


def _registry_for(runs: list[StrategyRun]) -> StrategyRegistry:
    registry = StrategyRegistry()
    for run in runs:
        registry.register(type(run.strategy))
    return registry
