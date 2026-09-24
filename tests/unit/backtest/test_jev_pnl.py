"""EM-162: Jev-on vs Jev-off compared through actual fills, drawdown and attribution."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.backtest.fingerprint import FingerprintMismatch
from emporos.backtest.jev_pnl import BacktestRunner, JevOnOffBacktestExperiment
from emporos.backtest.multi_engine import (
    MultiStrategyBacktestEngine,
    MultiStrategyBacktestResult,
    MultiStrategyBacktestSpec,
)
from emporos.domain.money import Money
from emporos.jev.config import JevConfig
from emporos.jev.models import CONFIRM, CONFIRMATION, REJECT, JevDecision, JevRequest
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from tests.support.backtest_engine import BuyThenSell
from tests.support.jev_backtest import scenario_engine, scenario_spec

DECIDED_AT = datetime(2026, 1, 5, 3, 46, tzinfo=UTC)


class _AlwaysConfirms:
    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=CONFIRM, confidence=Decimal("0.9"), provider="test", model="test-model",
            config_version=None, requested_at=DECIDED_AT, latency_ms=42, tokens_used=100,
        )  # fmt: skip


class _AlwaysRejects:
    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=REJECT, confidence=Decimal("0.9"), provider="test", model="test-model",
            config_version=None, requested_at=DECIDED_AT, latency_ms=17, tokens_used=55,
        )  # fmt: skip


class TestJevOnOffBacktestExperiment:
    async def test_a_confirming_jev_changes_nothing_but_its_own_reported_cost(self) -> None:
        BuyThenSell.reset()
        treatment_filter = JevMetaDecisionFilter(
            _AlwaysConfirms(), JevConfig(enabled=True, mode=CONFIRMATION)
        )
        experiment = JevOnOffBacktestExperiment(
            scenario_engine(None), scenario_engine(treatment_filter)
        )

        comparison = await experiment.run(scenario_spec())

        assert comparison.trade_count_delta == 0
        assert comparison.net_pnl_delta == Money.zero()
        assert comparison.baseline.jev.reviews == 0
        assert comparison.treatment.jev.reviews == 1
        assert comparison.treatment.jev.latency_ms == 42
        assert comparison.treatment.jev.tokens == 100
        assert comparison.treatment.jev.rejections == 0

    async def test_a_rejecting_jev_stops_the_trade_and_the_pnl_reflects_it(self) -> None:
        BuyThenSell.reset()
        treatment_filter = JevMetaDecisionFilter(
            _AlwaysRejects(), JevConfig(enabled=True, mode=CONFIRMATION)
        )
        experiment = JevOnOffBacktestExperiment(
            scenario_engine(None), scenario_engine(treatment_filter)
        )

        comparison = await experiment.run(scenario_spec())

        assert comparison.baseline.metrics.trades.count == 1
        assert comparison.treatment.metrics.trades.count == 0
        assert comparison.trade_count_delta == -1
        assert comparison.treatment.jev.rejections == 1
        # the baseline paid real costs on its one trade the rejected treatment never took
        assert comparison.net_pnl_delta != Money.zero()


class _Factory:
    """One set of collaborators; records what each arm was built with."""

    def __init__(self) -> None:
        self.built_with: list[JevMetaDecisionFilter | None] = []

    def build(self, jev_filter: JevMetaDecisionFilter | None) -> BacktestRunner:
        self.built_with.append(jev_filter)
        return scenario_engine(jev_filter)


class _RewritesTheSpec:
    """An arm that quietly ran something other than what it was given."""

    def __init__(self, inner: MultiStrategyBacktestEngine) -> None:
        self._inner = inner

    async def run(self, spec: MultiStrategyBacktestSpec) -> MultiStrategyBacktestResult:
        result = await self._inner.run(spec)
        return replace(result, spec=replace(spec, starting_cash=Money.of("99999")))


class TestIdenticalByConstruction:
    async def test_the_factory_builds_both_arms_differing_only_in_the_filter(self) -> None:
        BuyThenSell.reset()
        factory = _Factory()
        treatment_filter = JevMetaDecisionFilter(
            _AlwaysConfirms(), JevConfig(enabled=True, mode=CONFIRMATION)
        )

        comparison = await JevOnOffBacktestExperiment.from_factory(factory, treatment_filter).run(
            scenario_spec()
        )

        assert factory.built_with == [None, treatment_filter]
        assert comparison.trade_count_delta == 0

    async def test_the_comparison_carries_the_fingerprint_both_arms_were_verified_against(
        self,
    ) -> None:
        BuyThenSell.reset()
        experiment = JevOnOffBacktestExperiment(scenario_engine(None), scenario_engine(None))

        comparison = await experiment.run(scenario_spec())

        assert comparison.fingerprint.digest.startswith("sha256:")

    async def test_an_arm_that_ran_a_different_spec_is_refused(self) -> None:
        BuyThenSell.reset()
        experiment = JevOnOffBacktestExperiment(
            scenario_engine(None), _RewritesTheSpec(scenario_engine(None))
        )

        with pytest.raises(FingerprintMismatch, match="different assumptions"):
            await experiment.run(scenario_spec())

    async def test_arms_that_ran_different_strategies_are_refused(self) -> None:
        BuyThenSell.reset()

        class _OtherStrategy:
            async def run(self, spec: MultiStrategyBacktestSpec) -> MultiStrategyBacktestResult:
                result = await scenario_engine(None).run(spec)
                identity = replace(result.strategies[0], config_hash="sha256:other")
                return replace(result, strategies=(identity,))

        experiment = JevOnOffBacktestExperiment(scenario_engine(None), _OtherStrategy())

        with pytest.raises(FingerprintMismatch, match="different strategies"):
            await experiment.run(scenario_spec())
