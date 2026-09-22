from __future__ import annotations

from decimal import Decimal

from emporos.domain.candles import Timeframe
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.opportunity.scanner import OpportunityScanner, StrategySignal
from emporos.strategies.config import RiskSettings
from emporos.strategies.metadata import StrategyMetadata
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, ScriptedStrategy, make_signal


def _risk_settings() -> RiskSettings:
    return RiskSettings(
        max_position_value=Decimal(50000),
        max_open_positions=3,
        stop_loss_pct=Decimal(2),
        target_pct=Decimal(4),
    )


def _registry(metadata: StrategyMetadata | None = None) -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(ScriptedStrategy, metadata)
    return registry


def _entry(
    signal: Signal | None = None,
    strategy_name: str = "scripted",
    instrument_id: str = INSTRUMENT,
    timeframe: Timeframe = Timeframe.M5,
) -> StrategySignal:
    return StrategySignal(
        strategy_name=strategy_name,
        instrument_id=instrument_id,
        timeframe=timeframe,
        signal=signal,
        risk_settings=_risk_settings(),
    )


def test_no_signals_is_a_no_trade_scan() -> None:
    scanner = OpportunityScanner(_registry())
    result = scanner.scan([], regimes={}, ts=T0)
    assert result.is_no_trade
    assert result.candidates == ()
    assert result.rejected == ()


def test_a_missing_signal_produces_no_candidate_and_no_rejection() -> None:
    scanner = OpportunityScanner(_registry())
    result = scanner.scan([_entry(signal=None)], regimes={INSTRUMENT: MarketRegime.TRENDING}, ts=T0)
    assert result.is_no_trade
    assert result.rejected == ()


def test_an_eligible_signal_becomes_a_ranked_candidate() -> None:
    scanner = OpportunityScanner(_registry())
    signal = make_signal(price="100", side=OrderSide.BUY)

    result = scanner.scan(
        [_entry(signal=signal)], regimes={INSTRUMENT: MarketRegime.TRENDING}, ts=T0
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.strategy_name == "scripted"
    assert candidate.instrument_id == INSTRUMENT
    assert candidate.stop.amount == Decimal("98")
    assert candidate.target.amount == Decimal("104")


def test_an_exit_signal_never_becomes_a_candidate() -> None:
    scanner = OpportunityScanner(_registry())
    signal = make_signal(kind=SignalKind.EXIT)

    result = scanner.scan(
        [_entry(signal=signal)], regimes={INSTRUMENT: MarketRegime.TRENDING}, ts=T0
    )

    assert result.is_no_trade


def test_an_unclassified_regime_is_rejected_not_silently_dropped() -> None:
    scanner = OpportunityScanner(_registry())
    signal = make_signal()

    result = scanner.scan([_entry(signal=signal)], regimes={}, ts=T0)

    assert result.is_no_trade
    assert len(result.rejected) == 1
    assert "regime" in result.rejected[0].reason


def test_a_strategy_ineligible_for_the_current_regime_is_rejected() -> None:
    metadata = StrategyMetadata(
        version="v1",
        supported_timeframes=frozenset({Timeframe.M5}),
        supported_regimes=frozenset({MarketRegime.RANGING}),
    )
    scanner = OpportunityScanner(_registry(metadata))
    signal = make_signal()

    result = scanner.scan(
        [_entry(signal=signal)], regimes={INSTRUMENT: MarketRegime.TRENDING}, ts=T0
    )

    assert result.is_no_trade
    assert len(result.rejected) == 1
    assert "not eligible" in result.rejected[0].reason


def test_candidates_are_ranked_highest_score_first() -> None:
    scanner = OpportunityScanner(_registry())
    strong = make_signal(instrument_id=INSTRUMENT, price="100")
    weak = make_signal(instrument_id=OTHER_INSTRUMENT, price="200")

    result = scanner.scan(
        [
            _entry(signal=weak, instrument_id=OTHER_INSTRUMENT),
            _entry(signal=strong, instrument_id=INSTRUMENT),
        ],
        regimes={INSTRUMENT: MarketRegime.TRENDING, OTHER_INSTRUMENT: MarketRegime.TRENDING},
        ts=T0,
    )

    assert len(result.candidates) == 2
    assert result.candidates[0].score >= result.candidates[1].score


def test_a_short_signal_prices_stop_above_and_target_below_entry() -> None:
    scanner = OpportunityScanner(_registry())
    signal = make_signal(side=OrderSide.SELL, price="100")

    result = scanner.scan(
        [_entry(signal=signal)], regimes={INSTRUMENT: MarketRegime.TRENDING}, ts=T0
    )

    candidate = result.candidates[0]
    assert candidate.stop.amount == Decimal("102")
    assert candidate.target.amount == Decimal("96")
