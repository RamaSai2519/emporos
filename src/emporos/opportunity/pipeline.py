"""The runtime-agnostic core of multi-strategy opportunity selection (EM-152 / EM-158 / EM-163).

`OpportunityPipeline` is the one place bar-in, order-ready-signals-out logic lives so that
backtest, paper and live all make identical decisions from identical inputs — only the runtime
around it (which delivers bars, which broker fills orders) differs. It owns exactly three things
per closed bar: keep each instrument's regime current, drive every strategy watching that
instrument, and turn the entry signals that come back into a ranked, capital-allocated set via
`OpportunityScanner` and `PortfolioAllocator`. It does not place orders, does not know about a
broker, and does not decide execution timing — those stay with whatever runtime calls it.

Exit signals are never ranked or allocated: plan.md is explicit that a signal reducing exposure
must never be blocked by the same rules that gate an entry, so every exit a strategy emits is
returned unconditionally, in emission order, ahead of any decision this pipeline makes about
entries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.signals import Signal, SignalKind
from emporos.jev.config import JevConfig
from emporos.jev.null_provider import NullJevProvider
from emporos.opportunity.allocator import Allocation, AllocationConstraints, PortfolioAllocator
from emporos.opportunity.jev_filter import JevFilterResult, JevMetaDecisionFilter
from emporos.opportunity.scanner import OpportunityScanner, ScanResult, StrategySignal
from emporos.risk.snapshot import AccountFacts
from emporos.strategies.base import Strategy
from emporos.strategies.config import RiskSettings
from emporos.strategies.regime import MarketRegime

_JEV_DISABLED = JevMetaDecisionFilter(NullJevProvider(), JevConfig(enabled=False))

AccountFactsProvider = Callable[[], AccountFacts]


class RegimeSource(Protocol):
    """What the pipeline needs from a regime classifier — narrow enough that a test double or a
    future non-ATR-based classifier can stand in without depending on `MarketRegimeClassifier`
    itself."""

    def update(self, candle: Candle) -> MarketRegime | None: ...


@dataclass(frozen=True)
class StrategyRun:
    """One strategy already built (initialized) against one instrument, ready to receive bars."""

    strategy_name: str
    instrument_id: str
    timeframe: Timeframe
    strategy: Strategy
    risk_settings: RiskSettings


@dataclass(frozen=True)
class BarOutcome:
    exits: tuple[Signal, ...]
    scan: ScanResult
    jev: JevFilterResult
    allocations: tuple[Allocation, ...]

    @property
    def approved_signals(self) -> tuple[Signal, ...]:
        return tuple(allocation.signal for allocation in self.allocations)


class OpportunityPipeline:
    def __init__(
        self,
        runs: Sequence[StrategyRun],
        regimes: Mapping[str, RegimeSource],
        scanner: OpportunityScanner,
        allocator: PortfolioAllocator,
        account: AccountFactsProvider,
        constraints: AllocationConstraints,
        jev_filter: JevMetaDecisionFilter | None = None,
    ) -> None:
        self._by_instrument: dict[str, list[StrategyRun]] = {}
        for run in runs:
            self._by_instrument.setdefault(run.instrument_id, []).append(run)
        self._regimes = regimes
        self._scanner = scanner
        self._allocator = allocator
        self._account = account
        self._constraints = constraints
        # Disabled by default: EM-152 is explicit that the pipeline is fully functional with Jev
        # absent, so a caller that never mentions Jev gets exactly the pre-Jev ranking untouched.
        self._jev_filter = jev_filter or _JEV_DISABLED

    async def on_bars(self, candles: Sequence[Candle]) -> BarOutcome:
        """One evaluation tick: every instrument whose bar just closed at the same timestamp,
        delivered together so ranking and allocation see the whole universe snapshot at once,
        not one instrument greedily ahead of the next. A single-instrument runtime (or a test)
        may still call this with one candle; the semantics are the same either way."""
        if not candles:
            raise ValueError("on_bars needs at least one closed bar")
        regimes: dict[str, MarketRegime | None] = {}
        exits: list[Signal] = []
        entries: list[StrategySignal] = []
        ts = candles[0].ts

        for candle in candles:
            classifier = self._regimes.get(candle.instrument_id)
            regimes[candle.instrument_id] = (
                classifier.update(candle) if classifier is not None else None
            )
            for run in self._by_instrument.get(candle.instrument_id, ()):
                run.strategy.on_market_data(candle)
                while (signal := run.strategy.generate_signal()) is not None:
                    if signal.kind is SignalKind.EXIT:
                        exits.append(signal)
                    else:
                        entries.append(
                            StrategySignal(
                                strategy_name=run.strategy_name,
                                instrument_id=run.instrument_id,
                                timeframe=run.timeframe,
                                signal=signal,
                                risk_settings=run.risk_settings,
                            )
                        )

        scan = self._scanner.scan(entries, regimes=regimes, ts=ts)
        jev_result = await self._jev_filter.apply(scan.candidates)
        allocations = self._allocator.allocate(
            jev_result.candidates, account=self._account(), constraints=self._constraints
        )
        return BarOutcome(exits=tuple(exits), scan=scan, jev=jev_result, allocations=allocations)
