"""`BacktestJob` — everything between "run momentum_v1 over these dates" and the engine.

    dates ─▶ universe as of the first day ─▶ config resolved against IT ─▶ window ─▶ BacktestEngine

The composition root hands in the pieces that need I/O (candles, the instrument eras, a way to turn
a YAML file into a resolved config against a given instrument resolver, the fee schedules). The job
adds no I/O of its own, so it can be tested with doubles, and it records in the result every
assumption it had to make: which instruments were resolved without recorded history, and (through
the schedule source) which days were priced with a fee schedule that did not yet exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.engine import BacktestEngine, BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.integrity import ResearchIntegrityGate
from emporos.backtest.metrics.report import MetricsSettings
from emporos.backtest.progress import BacktestProgressSink
from emporos.backtest.provenance import ProvenanceSnapshotter
from emporos.backtest.settings import FillSettings
from emporos.backtest.universe import AsOfInstruments
from emporos.core.clock import IST
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.marketdata.session import SessionWindow
from emporos.persistence.candles import CandleReader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry


@dataclass(frozen=True)
class BacktestRequest:
    first_day: date  # IST trading days, both inclusive
    last_day: date
    starting_cash: Money
    fills: FillSettings = field(default_factory=FillSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    # Resolve instruments the master has no history for from their earliest known definition.
    assume_current_universe: bool = False
    # An explicit, recorded opt-in to run across a quarantined corporate-action day (EM-177).
    allow_quarantined_instruments: bool = False

    def __post_init__(self) -> None:
        if self.first_day > self.last_day:
            raise ValueError("a backtest's first day cannot be after its last")


class ResolverTickSizes:
    """`TickSizes` from the same instrument resolver the config was resolved with."""

    def __init__(self, resolver: InstrumentResolver) -> None:
        self._resolver = resolver

    def tick_size(self, instrument_id: str) -> Money:
        return self._resolver.by_id(instrument_id).tick_size


class BacktestJob:
    def __init__(
        self,
        reader: CandleReader,
        registry: StrategyRegistry,
        instruments: AsOfInstruments,
        config_for: Callable[[InstrumentResolver], ResolvedStrategyConfig],
        schedules: Callable[[], ScheduleSource],
        session: SessionWindow | None = None,
        progress: BacktestProgressSink | None = None,
        calendar: StoredTradingCalendar | None = None,
        quarantine: CorporateActionQuarantine | None = None,
    ) -> None:
        self._reader = reader
        self._registry = registry
        self._instruments = instruments
        self._config_for = config_for
        self._schedules = schedules
        self._session = session or SessionWindow()
        self._progress = progress
        self._calendar = calendar or StoredTradingCalendar()
        self._quarantine = quarantine or CorporateActionQuarantine()

    async def run(self, request: BacktestRequest) -> BacktestResult:
        moment = self._session.open_at(request.first_day)
        universe = self._instruments.as_of(
            moment, assume_earliest_before_history=request.assume_current_universe
        )
        config = self._config_for(universe.resolver)
        ResearchIntegrityGate(self._quarantine).check(
            config.instrument_ids, request.first_day, request.last_day,
            allow_quarantined=request.allow_quarantined_instruments,
        )  # fmt: skip
        provenance = ProvenanceSnapshotter().take(
            universe, config.instrument_ids, config.timeframe, request.first_day,
            request.last_day, self._quarantine, self._calendar.content_hash(),
        )  # fmt: skip
        engine = BacktestEngine(
            self._reader, self._registry, ResolverTickSizes(universe.resolver), self._schedules,
            progress=self._progress,
        )  # fmt: skip
        spec = BacktestSpec(
            config=config,
            window=self._window(request),
            starting_cash=request.starting_cash,
            fills=request.fills,
            metrics=request.metrics,
            assumptions=self._assumptions(config, universe.assumed_ids, request),
            provenance=provenance,
        )
        return await engine.run(spec)

    @staticmethod
    def _window(request: BacktestRequest) -> FeedWindow:
        """From midnight IST of the first day to midnight IST after the last, as UTC."""
        midnight = time(0, 0)
        start = datetime.combine(request.first_day, midnight, tzinfo=IST)
        end = datetime.combine(request.last_day + timedelta(days=1), midnight, tzinfo=IST)
        return FeedWindow(start.astimezone(UTC), end.astimezone(UTC))

    @staticmethod
    def _assumptions(
        config: ResolvedStrategyConfig, assumed_ids: frozenset[str], request: BacktestRequest
    ) -> tuple[str, ...]:
        leaned = [i for i in config.instrument_ids if i in assumed_ids]
        if leaned:
            return (
                f"universe: {len(leaned)} of {len(config.instrument_ids)} instrument(s) were "
                f"resolved from their EARLIEST recorded definition, because the instrument master "
                f"holds no history covering {request.first_day.isoformat()}; a delisting or symbol "
                "change before the master began recording is not represented (survivorship, "
                "EM-99 H8)",
            )
        return (
            f"universe: resolved from the instrument master as it stood on "
            f"{request.first_day.isoformat()} (instrument_versions)",
        )
