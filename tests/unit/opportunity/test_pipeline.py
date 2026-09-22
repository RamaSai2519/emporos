from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.core.alerts import AlertSink
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.jev.config import JevConfig
from emporos.jev.models import CONFIRMATION, REJECT, JevDecision, JevRequest
from emporos.opportunity.allocator import AllocationConstraints, PortfolioAllocator
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
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
    fail_in: str | None = None,
) -> StrategyRun:
    class _Named(ScriptedStrategy):
        name = strategy_name

    config = make_config(name=strategy_name, instruments=(instrument_id,))
    strategy = _Named(config, fail_in=fail_in)
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
    alerts: AlertSink | None = None,
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
        alerts=alerts,
    )


async def test_on_bars_rejects_an_empty_batch() -> None:
    pipeline = _pipeline([])
    with pytest.raises(ValueError, match="at least one"):
        await pipeline.on_bars([])


async def test_a_strategy_with_no_signal_produces_no_outcome() -> None:
    run = _strategy_run("alpha", INSTRUMENT)
    pipeline = _pipeline([run])

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.exits == ()
    assert outcome.allocations == ()


async def test_exit_signals_always_pass_through_unranked() -> None:
    exit_signal = make_signal(instrument_id=INSTRUMENT, kind=SignalKind.EXIT)
    run = _strategy_run("alpha", INSTRUMENT, signals=[exit_signal])
    pipeline = _pipeline([run])

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.exits == (exit_signal,)
    assert outcome.allocations == ()


async def test_an_entry_signal_with_a_classified_regime_is_allocated() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100", side=OrderSide.BUY)
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = _pipeline([run])

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert len(outcome.allocations) == 1
    assert outcome.allocations[0].candidate.strategy_name == "alpha"
    assert outcome.approved_signals[0].instrument_id == INSTRUMENT


async def test_an_entry_with_no_classified_regime_is_rejected_not_allocated() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = _pipeline([run], regime=None)

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.allocations == ()
    assert len(outcome.scan.rejected) == 1


async def test_no_regime_source_for_an_instrument_is_treated_as_unclassified() -> None:
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

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.allocations == ()


async def test_a_bar_for_an_instrument_with_no_strategy_runs_is_a_no_op() -> None:
    run = _strategy_run("alpha", INSTRUMENT)
    pipeline = _pipeline([run])

    outcome = await pipeline.on_bars([bar_at(OTHER_INSTRUMENT)])

    assert outcome.exits == ()
    assert outcome.allocations == ()


async def test_a_multi_instrument_batch_ranks_across_the_whole_universe_not_per_instrument() -> (
    None
):
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

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT), bar_at(OTHER_INSTRUMENT)])

    assert len(outcome.allocations) == 1


class _RejectingProvider:
    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=REJECT,
            confidence=None,
            provider="test",
            model=None,
            config_version=None,
            requested_at=datetime.now(UTC),
            latency_ms=0,
        )


async def test_an_enabled_jev_filter_can_reject_what_the_scan_ranked() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    registry = _registry_for([run])
    pipeline = OpportunityPipeline(
        runs=[run],
        regimes={INSTRUMENT: _FixedRegime(MarketRegime.TRENDING)},
        scanner=OpportunityScanner(registry),
        allocator=PortfolioAllocator(),
        account=lambda: AccountFacts(),
        constraints=_constraints(),
        jev_filter=JevMetaDecisionFilter(
            _RejectingProvider(), JevConfig(enabled=True, mode=CONFIRMATION)
        ),
    )

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.allocations == ()
    assert len(outcome.jev.rejected) == 1
    assert len(outcome.scan.candidates) == 1  # the scan still ranked it; Jev is what dropped it


async def test_jev_disabled_by_default_leaves_allocations_unaffected() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    run = _strategy_run("alpha", INSTRUMENT, signals=[entry])
    pipeline = _pipeline([run])

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.jev.reviews == ()
    assert len(outcome.allocations) == 1


class _Alerts:
    """An `AlertSink` that remembers what it was told (mirrors `backtest.alerts.CollectedAlerts`,
    kept local so opportunity tests do not reach into the backtest package for a test double)."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def raise_alert(self, name: str, message: str) -> None:
        self.alerts.append((name, message))


async def test_a_strategy_that_raises_in_on_market_data_is_isolated_not_propagated() -> None:
    """AGENTS.md: 'a strategy handler that raises is isolated... must never kill the session.'
    `OpportunityPipeline` drives raw `Strategy` objects with no `StrategyRunner` wrapping it, so
    it must give the same guarantee itself."""
    bad = _strategy_run("alpha", INSTRUMENT, fail_in="on_market_data")
    good = _strategy_run(
        "beta", OTHER_INSTRUMENT, signals=[make_signal(instrument_id=OTHER_INSTRUMENT, price="100")]
    )
    alerts = _Alerts()
    pipeline = _pipeline([bad, good], alerts=alerts)

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT), bar_at(OTHER_INSTRUMENT)])

    assert len(outcome.allocations) == 1
    assert outcome.allocations[0].candidate.strategy_name == "beta"
    assert len(alerts.alerts) == 1
    assert alerts.alerts[0][0] == "strategy_halted"
    assert "alpha" in alerts.alerts[0][1]


async def test_a_halted_strategy_stays_halted_on_later_ticks() -> None:
    bad = _strategy_run("alpha", INSTRUMENT, fail_in="on_market_data")
    alerts = _Alerts()
    pipeline = _pipeline([bad], alerts=alerts)

    await pipeline.on_bars([bar_at(INSTRUMENT)])
    await pipeline.on_bars([bar_at(INSTRUMENT, minutes=5)])

    assert len(alerts.alerts) == 1  # the second tick skipped the halted strategy, not re-raised


async def test_a_fault_in_generate_signal_halts_that_strategy_without_propagating() -> None:
    run = _strategy_run("alpha", INSTRUMENT, fail_in="generate_signal")
    alerts = _Alerts()
    pipeline = _pipeline([run], alerts=alerts)

    outcome = await pipeline.on_bars([bar_at(INSTRUMENT)])

    assert outcome.exits == () and outcome.allocations == ()
    assert len(alerts.alerts) == 1
    assert alerts.alerts[0][0] == "strategy_halted"


def _registry_for(runs: list[StrategyRun]) -> StrategyRegistry:
    registry = StrategyRegistry()
    for run in runs:
        registry.register(type(run.strategy))
    return registry
