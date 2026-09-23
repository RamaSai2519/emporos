"""The shadow backtest: the exact config a paper run used, replayed over the exact session.

    paper StrategyRunRecord ─▶ restore config_snapshot ─▶ (hash must reproduce) ─▶ BacktestEngine
                                                                     ▲
             the run's session day, the paper account's cash, a journal sink ┘

The candles are the ones persisted from the live feed, so both sides see the same bars. A config
whose recorded hash cannot be reproduced is refused (`ConfigDrift`); everything else about the run
(risk limits, fee schedule, engine wiring) comes from the injected `ShadowEngines`, which the
composition root builds the same way it builds the paper worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.journal import BacktestEventSink, RecordingSink
from emporos.backtest.metrics.report import MetricsSettings
from emporos.backtest.provenance import ResearchProvenance
from emporos.backtest.settings import FillSettings
from emporos.core.clock import IST
from emporos.domain.money import Money
from emporos.parity.errors import ConfigDrift
from emporos.persistence.records import StrategyRunRecord
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import ParametersCatalog, StrategyConfigError
from emporos.strategies.snapshot import ConfigSnapshotter, SnapshotIntegrityError


class ShadowEngine(Protocol):
    async def run(self, spec: BacktestSpec) -> BacktestResult: ...


class ShadowEngines(Protocol):
    def build(self, session_date: date, sink: BacktestEventSink) -> ShadowEngine:
        """An engine for one session day, reporting to `sink`."""
        ...


class ProvenanceSource(Protocol):
    def provenance_for(
        self, config: ResolvedStrategyConfig, session_date: date
    ) -> ResearchProvenance | None: ...


@dataclass(frozen=True)
class ShadowRun:
    run: StrategyRunRecord
    session_date: date
    config: ResolvedStrategyConfig
    result: BacktestResult
    journal: RecordingSink


class ShadowBacktest:
    def __init__(
        self,
        engines: ShadowEngines,
        catalog: ParametersCatalog,
        starting_cash: Money,
        snapshotter: ConfigSnapshotter | None = None,
        fills: FillSettings | None = None,
        metrics: MetricsSettings | None = None,
        provenance: ProvenanceSource | None = None,
    ) -> None:
        self._engines = engines
        self._catalog = catalog
        self._cash = starting_cash
        self._snapshotter = snapshotter or ConfigSnapshotter()
        self._fills = fills or FillSettings()
        self._metrics = metrics or MetricsSettings()
        self._provenance = provenance

    async def run(self, run: StrategyRunRecord) -> ShadowRun:
        config = self._restore(run)
        session_date = date.fromisoformat(run.session_date)
        journal = RecordingSink()
        spec = BacktestSpec(
            config=config,
            window=self._window(session_date),
            starting_cash=self._cash,
            fills=self._fills,
            metrics=self._metrics,
            assumptions=(f"shadow of paper run {run.id} on {run.session_date}",),
            provenance=None
            if self._provenance is None
            else self._provenance.provenance_for(config, session_date),
        )
        result = await self._engines.build(session_date, journal).run(spec)
        return ShadowRun(run, session_date, config, result, journal)

    def _restore(self, run: StrategyRunRecord) -> ResolvedStrategyConfig:
        if not run.config_hash:
            raise ConfigDrift(f"run {run.id} recorded no config hash to reproduce")
        try:
            config = self._snapshotter.restore(
                run.config_snapshot, self._catalog, expected_hash=run.config_hash
            )
        except (SnapshotIntegrityError, StrategyConfigError) as error:
            raise ConfigDrift(f"run {run.id}: {error}") from error
        if self._snapshotter.take(config).content_hash != run.config_hash:
            raise ConfigDrift(f"run {run.id}: the restored config does not reproduce its hash")
        return config

    @staticmethod
    def _window(day: date) -> FeedWindow:
        """Midnight IST of the session day to midnight IST after it, as UTC."""
        start = datetime.combine(day, time(0, 0), tzinfo=IST)
        end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=IST)
        return FeedWindow(start.astimezone(UTC), end.astimezone(UTC))
