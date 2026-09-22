from __future__ import annotations

from decimal import Decimal

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.opportunity.allocator import AllocationConstraints, PortfolioAllocator
from emporos.opportunity.audit import OpportunityAuditLog
from emporos.opportunity.deployment_gate import DeploymentGate
from emporos.opportunity.pipeline import OpportunityPipeline, StrategyRun
from emporos.opportunity.runner import OpportunityRunner
from emporos.opportunity.scanner import OpportunityScanner
from emporos.risk.snapshot import AccountFacts
from emporos.session.signal_path import Submission
from emporos.strategies.config import RiskSettings
from emporos.strategies.metadata import DeploymentStatus, StrategyMetadata
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from tests.support.records import RecordFactory
from tests.support.strategies import (
    INSTRUMENT,
    T0,
    ScriptedStrategy,
    bar_at,
    make_config,
    make_context,
    make_signal,
)


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
    def update(self, candle: object) -> MarketRegime | None:
        return MarketRegime.TRENDING


class _RecordingSink:
    """A `GatedSignalSink` double: records every signal it was asked to route, and replies
    according to a scripted plan (placed, rejected, or refused) keyed by call order."""

    def __init__(self, replies: list[Submission]) -> None:
        self._replies = list(replies)
        self.signals: list[Signal] = []

    async def submit_as(self, signal: Signal, signal_id: str | None = None) -> Submission:
        self.signals.append(signal)
        return self._replies.pop(0)


def _pipeline(
    entry_signal: Signal | None, exit_signal: Signal | None = None
) -> OpportunityPipeline:
    config = make_config(name="scripted", instruments=(INSTRUMENT,))
    strategy = ScriptedStrategy(config)
    strategy.initialize(make_context(FixedClock(T0), config=config))
    signals = [s for s in (exit_signal, entry_signal) if s is not None]
    strategy._outbox = signals
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
    )


async def test_an_approved_entry_is_submitted_through_the_sink() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100", side=OrderSide.BUY)
    pipeline = _pipeline(entry)
    order = RecordFactory().order()
    sink = _RecordingSink([Submission(signal_id="s1", order=order)])
    runner = OpportunityRunner(pipeline, sink)

    outcome = await runner.on_bars([bar_at(INSTRUMENT)])

    assert len(sink.signals) == 1
    assert sink.signals[0].instrument_id == INSTRUMENT
    assert len(outcome.placed) == 1
    assert outcome.submissions[0].order is order


async def test_an_exit_is_submitted_ahead_of_any_entry() -> None:
    exit_signal = make_signal(instrument_id=INSTRUMENT, kind=SignalKind.EXIT)
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    pipeline = _pipeline(entry, exit_signal)
    replies = [Submission(signal_id="exit"), Submission(signal_id="entry")]
    sink = _RecordingSink(replies)
    runner = OpportunityRunner(pipeline, sink)

    await runner.on_bars([bar_at(INSTRUMENT)])

    assert sink.signals[0].kind is SignalKind.EXIT


async def test_a_rejected_submission_is_not_counted_as_placed() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    pipeline = _pipeline(entry)
    sink = _RecordingSink([Submission(signal_id="s1")])  # no order: rejected/refused
    runner = OpportunityRunner(pipeline, sink)

    outcome = await runner.on_bars([bar_at(INSTRUMENT)])

    assert outcome.placed == ()
    assert len(outcome.submissions) == 1


async def test_no_signals_means_nothing_is_submitted() -> None:
    pipeline = _pipeline(None)
    sink = _RecordingSink([])
    runner = OpportunityRunner(pipeline, sink)

    outcome = await runner.on_bars([bar_at(INSTRUMENT)])

    assert outcome.submissions == ()
    assert sink.signals == []


async def test_a_deployment_gate_can_scale_down_or_drop_an_entry_before_submission() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    pipeline = _pipeline(entry)
    registry = StrategyRegistry()
    registry.register(
        ScriptedStrategy,
        StrategyMetadata(
            version="v1",
            supported_timeframes=frozenset(),
            supported_regimes=frozenset(),
            deployment_status=DeploymentStatus.LIVE_CONSERVATIVE,
        ),
    )
    gate = DeploymentGate(registry, conservative_fraction=Decimal("0.5"))
    sink = _RecordingSink([Submission(signal_id="s1")])
    runner = OpportunityRunner(pipeline, sink, deployment_gate=gate)

    outcome = await runner.on_bars([bar_at(INSTRUMENT)])

    unscaled_quantity = outcome.pipeline.allocations[0].quantity
    assert sink.signals[0].quantity == unscaled_quantity // 2


async def test_a_deployment_gate_can_drop_an_ineligible_strategys_entry() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100", quantity=100)
    pipeline = _pipeline(entry)
    registry = StrategyRegistry()
    registry.register(
        ScriptedStrategy,
        StrategyMetadata(
            version="v1",
            supported_timeframes=frozenset(),
            supported_regimes=frozenset(),
            deployment_status=DeploymentStatus.PAPER,
        ),
    )
    gate = DeploymentGate(registry)
    sink = _RecordingSink([])
    runner = OpportunityRunner(pipeline, sink, deployment_gate=gate)

    outcome = await runner.on_bars([bar_at(INSTRUMENT)])

    assert outcome.submissions == ()
    assert sink.signals == []


async def test_an_audit_log_records_the_full_pipeline_outcome_before_the_deployment_gate() -> None:
    entry = make_signal(instrument_id=INSTRUMENT, price="100")
    pipeline = _pipeline(entry)
    sink = _RecordingSink([Submission(signal_id="s1")])

    class _FakeStore:
        def __init__(self) -> None:
            self.inserted: list[object] = []

        async def insert(self, record: object) -> None:
            self.inserted.append(record)

    store = _FakeStore()
    audit_log = OpportunityAuditLog(store, IdGenerator())
    runner = OpportunityRunner(pipeline, sink, audit_log=audit_log)

    await runner.on_bars([bar_at(INSTRUMENT)])

    assert len(store.inserted) == 1
