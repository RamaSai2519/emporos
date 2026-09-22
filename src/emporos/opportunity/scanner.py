"""The opportunity-selection scan (EM-156): Strategy x Stock x Timeframe x Regime evaluation.

`OpportunityScanner` does not drive bars — the runner/engine (backtest or live) already does
that, delivering each closed bar to every strategy and collecting whatever `generate_signal()`
returns. The scanner's job starts there: given one signal per (strategy, instrument) for this
evaluation point, decide whether the strategy was even eligible to speak (`StrategyMetadata`),
turn an eligible signal into a comparable `OpportunityCandidate` using its own risk settings, and
rank what survives. An instrument with no eligible signal, or a strategy that produced nothing,
naturally yields no candidate — "no trade" needs no special case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.opportunity.models import OpportunityCandidate, RejectedCandidate
from emporos.strategies.config import RiskSettings
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry

_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class StrategySignal:
    """One strategy's output for one instrument at this evaluation point, plus the risk
    settings and timeframe its config declared — everything the scanner needs to check
    eligibility and price a candidate."""

    strategy_name: str
    instrument_id: str
    timeframe: Timeframe
    signal: Signal | None
    risk_settings: RiskSettings


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[OpportunityCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]

    @property
    def is_no_trade(self) -> bool:
        return not self.candidates


class OpportunityScanner:
    """Injected with the registry so eligibility (`StrategyMetadata.supports`) is checked
    against whatever is actually registered, never a hardcoded assumption."""

    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    def scan(
        self,
        signals: Sequence[StrategySignal],
        *,
        regimes: Mapping[str, MarketRegime | None],
        ts: datetime,
    ) -> ScanResult:
        candidates: list[OpportunityCandidate] = []
        rejected: list[RejectedCandidate] = []
        for entry in signals:
            regime = regimes.get(entry.instrument_id)
            outcome = self._evaluate(entry, regime=regime, ts=ts)
            if isinstance(outcome, OpportunityCandidate):
                candidates.append(outcome)
            elif outcome is not None:
                rejected.append(outcome)
        ranked = tuple(sorted(candidates, key=lambda c: c.score, reverse=True))
        return ScanResult(candidates=ranked, rejected=tuple(rejected))

    def _evaluate(
        self,
        entry: StrategySignal,
        *,
        regime: MarketRegime | None,
        ts: datetime,
    ) -> OpportunityCandidate | RejectedCandidate | None:
        if entry.signal is None:
            return None  # the strategy had nothing to say; not a rejection, just silence
        if entry.signal.kind is not SignalKind.ENTRY:
            return None  # exits bypass opportunity ranking entirely — they only reduce exposure
        metadata = self._registry.metadata(entry.strategy_name)
        if regime is None:
            return RejectedCandidate(
                strategy_name=entry.strategy_name,
                instrument_id=entry.instrument_id,
                reason="no regime classification available yet",
                ts=ts,
            )
        if not metadata.supports(
            timeframe=entry.timeframe,
            regime=regime,
            instrument_id=entry.instrument_id,
        ):
            return RejectedCandidate(
                strategy_name=entry.strategy_name,
                instrument_id=entry.instrument_id,
                reason=f"strategy is not eligible for {regime.value} regime on this instrument",
                ts=ts,
            )
        return self._to_candidate(entry, regime=regime)

    @staticmethod
    def _to_candidate(entry: StrategySignal, *, regime: MarketRegime) -> OpportunityCandidate:
        signal = entry.signal
        assert signal is not None  # narrowed by the caller
        entry_price = signal.limit_price.amount
        stop_fraction = entry.risk_settings.stop_loss_pct / _HUNDRED
        target_fraction = entry.risk_settings.target_pct / _HUNDRED
        if signal.side is OrderSide.BUY:
            stop = entry_price * (Decimal(1) - stop_fraction)
            target = entry_price * (Decimal(1) + target_fraction)
        else:
            stop = entry_price * (Decimal(1) + stop_fraction)
            target = entry_price * (Decimal(1) - target_fraction)
        return OpportunityCandidate(
            strategy_name=entry.strategy_name,
            instrument_id=entry.instrument_id,
            timeframe=entry.timeframe,
            signal=signal,
            entry=signal.limit_price,
            stop=Money(stop),
            target=Money(target),
            regime=regime,
            generated_at=signal.ts,
        )
