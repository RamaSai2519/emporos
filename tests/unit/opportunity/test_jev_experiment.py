from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.jev.config import JevConfig
from emporos.jev.models import CONFIRM, CONFIRMATION, JevDecision, JevRequest
from emporos.opportunity.allocator import AllocationConstraints, PortfolioAllocator
from emporos.opportunity.jev_experiment import JevOnOffExperiment, JevRunSummary
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.opportunity.pipeline import OpportunityPipeline, StrategyRun
from emporos.opportunity.scanner import OpportunityScanner
from emporos.risk.snapshot import AccountFacts
from emporos.strategies.config import RiskSettings
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from tests.support.strategies import (
    INSTRUMENT,
    T0,
    ScriptedStrategy,
    bar_at,
    make_config,
    make_context,
    make_signal,
)

DECIDED_AT = datetime(2026, 1, 5, 3, 46, tzinfo=UTC)


def _risk_settings() -> RiskSettings:
    return RiskSettings(
        max_position_value=Decimal(50000),
        max_open_positions=3,
        stop_loss_pct=Decimal(2),
        target_pct=Decimal(4),
    )


def _constraints() -> AllocationConstraints:
    return AllocationConstraints(
        production_capital=Money.of("50000"),
        max_simultaneous_positions=3,
        max_risk_per_trade=Money.of("1000"),
        max_portfolio_risk=Money.of("3000"),
    )


class _FixedRegime:
    def update(self, candle: Candle) -> MarketRegime | None:
        return MarketRegime.TRENDING


def _build_side(jev_filter: JevMetaDecisionFilter | None) -> OpportunityPipeline:
    """A fresh strategy instance and registry per side, so treatment's Jev-driven rejections
    never leak state back into baseline — exactly the independence the module docstring asks
    the caller to guarantee."""
    config = make_config(name="scripted", instruments=(INSTRUMENT,))
    strategy = ScriptedStrategy(config)
    strategy.initialize(make_context(FixedClock(T0), config=config))
    strategy._outbox = [make_signal(instrument_id=INSTRUMENT, price="100")]
    registry = StrategyRegistry()
    registry.register(ScriptedStrategy)
    run = StrategyRun("scripted", INSTRUMENT, Timeframe.M5, strategy, _risk_settings())
    return OpportunityPipeline(
        runs=[run],
        regimes={INSTRUMENT: _FixedRegime()},
        scanner=OpportunityScanner(registry),
        allocator=PortfolioAllocator(),
        account=lambda: AccountFacts(),
        constraints=_constraints(),
        jev_filter=jev_filter,
    )


class _AlwaysConfirms:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, request: JevRequest) -> JevDecision:
        self.calls += 1
        return JevDecision(
            decision=CONFIRM,
            confidence=Decimal("0.9"),
            provider="test",
            model="test-model",
            config_version=None,
            requested_at=DECIDED_AT,
            latency_ms=42,
            tokens_used=100,
        )


async def test_baseline_and_treatment_run_independently_over_the_same_bars() -> None:
    provider = _AlwaysConfirms()
    baseline = _build_side(None)
    treatment = _build_side(
        JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    )
    experiment = JevOnOffExperiment(baseline, treatment)

    report = await experiment.run([[bar_at(INSTRUMENT)]])

    assert len(report.baseline) == 1
    assert len(report.treatment) == 1
    assert report.baseline[0].jev_reviews == 0  # baseline never asked Jev anything
    assert report.treatment[0].jev_reviews == 1
    assert provider.calls == 1


async def test_treatment_reports_jev_latency_and_token_cost() -> None:
    provider = _AlwaysConfirms()
    baseline = _build_side(None)
    treatment = _build_side(
        JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    )
    experiment = JevOnOffExperiment(baseline, treatment)

    report = await experiment.run([[bar_at(INSTRUMENT)]])

    assert report.total_jev_latency_ms == 42
    assert report.total_jev_tokens == 100
    assert report.total_jev_rejections == 0


async def test_allocation_count_delta_is_zero_when_jev_confirms_everything() -> None:
    provider = _AlwaysConfirms()
    baseline = _build_side(None)
    treatment = _build_side(
        JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    )
    experiment = JevOnOffExperiment(baseline, treatment)

    report = await experiment.run([[bar_at(INSTRUMENT)]])

    assert report.allocation_count_delta == 0


async def test_an_empty_batch_in_the_sequence_is_refused() -> None:
    experiment = JevOnOffExperiment(_build_side(None), _build_side(None))

    with pytest.raises(ValueError, match="empty"):
        await experiment.run([[]])


class TestJevRunSummary:
    async def test_add_accumulates_reviews_latency_and_tokens_across_bar_batches(self) -> None:
        provider = _AlwaysConfirms()
        treatment = _build_side(
            JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
        )
        summary = JevRunSummary()

        summary = summary.add(await treatment.on_bars([bar_at(INSTRUMENT)]))

        assert summary.reviews == 1
        assert summary.rejections == 0
        assert summary.latency_ms == 42
        assert summary.tokens == 100

    def test_zero_is_the_default(self) -> None:
        assert JevRunSummary() == JevRunSummary(0, 0, 0, 0)
