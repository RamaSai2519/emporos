from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.feed import FeedWindow
from emporos.backtest.fingerprint import (
    ExperimentFingerprinter,
    FingerprintContext,
    FingerprintMismatch,
)
from emporos.backtest.metrics.report import MetricsSettings
from emporos.backtest.multi_engine import MultiStrategyBacktestSpec
from emporos.backtest.provenance import ResearchProvenance
from emporos.backtest.settings import FillSettings
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.research_experiments import DatePair
from emporos.opportunity.allocator import AllocationConstraints
from tests.support.strategies import INSTRUMENT, T0, make_config

_CONSTRAINTS = AllocationConstraints(
    production_capital=Money.of("5000"),
    max_simultaneous_positions=5,
    max_risk_per_trade=Money.of("500"),
    max_portfolio_risk=Money.of("2000"),
)


def _spec(**overrides: object) -> MultiStrategyBacktestSpec:
    base = MultiStrategyBacktestSpec(
        configs=[make_config(name="momentum_v1", instruments=(INSTRUMENT,))],
        window=FeedWindow(T0, T0 + timedelta(days=5)),
        starting_cash=Money.of("100000"),
        constraints=_CONSTRAINTS,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _provenance(universe_hash: str = "sha256:u") -> ResearchProvenance:
    return ResearchProvenance(
        schema_version=1,
        dataset_timeframe=Timeframe.M5,
        dataset_first=date(2026, 1, 1),
        dataset_last=date(2026, 3, 1),
        universe_hash=universe_hash,
        calendar_version="sha256:c",
        quarantine_hash="sha256:q",
        assumed_instrument_ids=(),
    )


def _digest(spec: MultiStrategyBacktestSpec, context: FingerprintContext | None = None) -> str:
    return ExperimentFingerprinter().fingerprint(spec, context).digest


def test_the_same_spec_hashes_the_same() -> None:
    assert _digest(_spec()) == _digest(_spec())


@pytest.mark.parametrize(
    "change",
    [
        {"starting_cash": Money.of("100001")},
        {"window": FeedWindow(T0, T0 + timedelta(days=6))},
        {"constraints": replace(_CONSTRAINTS, max_simultaneous_positions=4)},
        {"fills": FillSettings(slippage_bps=Decimal("5"))},
        {"metrics": MetricsSettings(annualisation_days=250)},
        {"warmup_bars": 10},
        {"warmup_lookback": timedelta(days=5)},
        {"assumptions": ("a note",)},
    ],
)
def test_every_spec_component_changes_the_fingerprint(change: dict[str, object]) -> None:
    assert _digest(_spec(**change)) != _digest(_spec())


def test_a_different_strategy_config_changes_the_fingerprint() -> None:
    other = _spec(configs=[make_config(name="momentum_v1", instruments=("NSE:TCS-EQ",))])

    assert _digest(other) != _digest(_spec())


def test_the_context_is_part_of_the_fingerprint() -> None:
    base = _digest(_spec(), FingerprintContext())

    assert _digest(_spec(), FingerprintContext(fee_schedule_id="angelone-2026")) != base
    assert _digest(_spec(), FingerprintContext(provenance=_provenance())) != base
    assert _digest(_spec(), FingerprintContext(provenance=_provenance("sha256:other"))) != _digest(
        _spec(), FingerprintContext(provenance=_provenance())
    )
    assert (
        _digest(_spec(), FingerprintContext(holdout=DatePair(date(2026, 2, 1), date(2026, 3, 1))))
        != base
    )


def test_verify_accepts_the_same_assumptions_and_refuses_different_ones() -> None:
    fingerprint = ExperimentFingerprinter().fingerprint(_spec())

    fingerprint.verify(_spec(), FingerprintContext())
    with pytest.raises(FingerprintMismatch):
        fingerprint.verify(_spec(warmup_bars=10), FingerprintContext())
