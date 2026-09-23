"""Compares a paper day with its shadow backtest and keeps the reports (EM-185).

    daily(day):   each paper run of the day ─▶ shadow backtest ─▶ SessionParity ─▶ DAILY report
    roll_up:      the stored dailies of that config ─▶ CUMULATIVE report (and WEEKLY on the week's
                  last session) — aggregated from the stored rows, never by re-running a shadow

It only reads the database and holds no broker: whatever it does, trading is untouched. A run whose
config cannot be reproduced is reported as skipped with its reason, not compared against a
different config.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.domain.experiments import Verdict
from emporos.domain.parity import ParityKind, ParityReport
from emporos.domain.verdicts import GateFinding
from emporos.marketdata.session import SessionWindow
from emporos.parity.codec import SessionParityCodec
from emporos.parity.errors import ConfigDrift
from emporos.parity.inputs import PaperRunSource
from emporos.parity.ledger import SessionParity
from emporos.parity.metrics import ParityAnalyzer, ParityMetrics
from emporos.parity.outcomes import BacktestOutcomes, BacktestSignals, PaperOutcomes, PaperSignals
from emporos.parity.report import MetricsTable
from emporos.parity.session import SessionKey, SessionParityBuilder, SideInputs
from emporos.parity.shadow import ShadowBacktest
from emporos.parity.trades import PaperTrades
from emporos.parity.verdict import ParityPolicy
from emporos.persistence.records import StrategyRunRecord
from emporos.strategies.snapshot import ConfigSnapshotter


class ParityReports(Protocol):
    async def append(self, report: ParityReport, report_id: str) -> bool: ...

    async def dailies(self, strategy: str, behaviour_hash: str) -> list[ParityReport]: ...

    async def daily_on(self, session_date: date) -> list[ParityReport]: ...


@dataclass(frozen=True)
class SkippedRun:
    run_id: str
    reason: str


@dataclass(frozen=True)
class DailyOutcome:
    session_date: date
    reports: tuple[ParityReport, ...]
    already_reported: int
    skipped: tuple[SkippedRun, ...]
    rolled_up: tuple[ParityReport, ...] = ()


class ParityService:
    def __init__(
        self,
        source: PaperRunSource,
        shadow: ShadowBacktest,
        reports: ParityReports,
        policy: ParityPolicy,
        window: SessionWindow,
        clock: Clock,
        ids: IdGenerator,
        alerts: AlertSink,
        analyzer: ParityAnalyzer | None = None,
        builder: SessionParityBuilder | None = None,
        codec: SessionParityCodec | None = None,
        snapshotter: ConfigSnapshotter | None = None,
    ) -> None:
        self._source = source
        self._shadow = shadow
        self._reports = reports
        self._policy = policy
        self._window = window
        self._clock = clock
        self._ids = ids
        self._alerts = alerts
        self._analyzer = analyzer or ParityAnalyzer()
        self._builder = builder or SessionParityBuilder()
        self._codec = codec or SessionParityCodec()
        self._snapshotter = snapshotter or ConfigSnapshotter()
        self._table = MetricsTable()
        self._paper_signals = PaperSignals()
        self._paper_outcomes = PaperOutcomes()
        self._paper_trades = PaperTrades()

    async def daily(self, day: date, roll_up: bool = True) -> DailyOutcome:
        existing = {(r.strategy, r.behaviour_hash) for r in await self._reports.daily_on(day)}
        made: list[ParityReport] = []
        skipped: list[SkippedRun] = []
        already = 0
        for run in await self._source.runs_on(day.isoformat()):
            try:
                report = await self._one_run(run, day, existing)
            except ConfigDrift as error:
                skipped.append(SkippedRun(run.id, str(error)))
                continue
            if report is None:
                already += 1
            else:
                made.append(report)
        rolled = await self._roll_up(made, day) if roll_up else []
        return DailyOutcome(day, tuple(made), already, tuple(skipped), tuple(rolled))

    # --- one run ----------------------------------------------------------------------------
    async def weekly(self, day: date) -> tuple[ParityReport, ...]:
        """Rebuild the ISO week's WEEKLY report (and the CUMULATIVE one) from the stored dailies."""
        monday = day - timedelta(days=day.weekday())
        dailies = [r for n in range(7) for r in await self._reports.daily_on(monday + timedelta(n))]
        return tuple(await self._roll_up(dailies, day, force_weekly=True))

    def assemble(self, sessions: Sequence[SessionParity], kind: ParityKind) -> ParityReport:
        """A report over any set of stored sessions, not persisted (for export and display)."""
        return self._report(kind, sessions)

    async def _one_run(
        self, run: StrategyRunRecord, day: date, existing: set[tuple[str, str]]
    ) -> ParityReport | None:
        recorded = (str(run.config_snapshot.get("name")), self._recorded_behaviour_hash(run))
        if recorded in existing:
            return None  # already reported for this configuration: the first stands, and free
        shadow = await self._shadow.run(run)
        strategy = shadow.config.name
        behaviour_hash = self._snapshotter.take(shadow.config).behaviour_hash
        data = await self._source.load(run)
        cash = shadow.result.spec.starting_cash
        session = self._builder.build(
            SessionKey(strategy, run.id, day, behaviour_hash, cash.amount),
            SideInputs(
                self._paper_signals.points(data),
                self._paper_outcomes.by_signal(data),
                self._paper_trades.build(data, cash),
            ),
            SideInputs(
                BacktestSignals().points(shadow.journal),
                BacktestOutcomes().by_ref(shadow.journal),
                shadow.result.trades,
            ),
        )
        report = self._report(ParityKind.DAILY, [session], self._codec.to_document(session))
        if await self._reports.append(report, self._ids.new_ulid()):
            self._alert_if_rejected(report)
            return report
        return None

    def _recorded_behaviour_hash(self, run: StrategyRunRecord) -> str:
        """Behaviour hash straight from the recorded document, so a day that is already reported
        is recognised without running (and paying for) a shadow backtest."""
        document = {k: v for k, v in run.config_snapshot.items() if k != "enabled"}
        return self._snapshotter.hash_of(document)

    # --- roll-ups ---------------------------------------------------------------------------
    async def _roll_up(
        self, made: Sequence[ParityReport], day: date, force_weekly: bool = False
    ) -> list[ParityReport]:
        rolled: list[ParityReport] = []
        for strategy, behaviour_hash in sorted({(r.strategy, r.behaviour_hash) for r in made}):
            dailies = await self._reports.dailies(strategy, behaviour_hash)
            sessions = [self._codec.from_document(d.payload) for d in dailies]
            kinds = [(ParityKind.CUMULATIVE, sessions)]
            if force_weekly or self._last_session_of_week(day):
                week = day.isocalendar()[:2]
                weekly = [s for s in sessions if s.session_date.isocalendar()[:2] == week]
                kinds.append((ParityKind.WEEKLY, weekly))
            for kind, chosen in kinds:
                report = self._report(kind, chosen)
                if await self._reports.append(report, self._ids.new_ulid()):
                    rolled.append(report)
                    self._alert_if_rejected(report)
        return rolled

    def _last_session_of_week(self, day: date) -> bool:
        return self._window.next_day(day).isocalendar()[:2] != day.isocalendar()[:2]

    def _report(
        self,
        kind: ParityKind,
        sessions: Sequence[SessionParity],
        payload: dict[str, object] | None = None,
    ) -> ParityReport:
        metrics: ParityMetrics = self._analyzer.metrics(sessions)
        verdict = self._policy.classify(metrics)
        first = min(s.session_date for s in sessions)
        last = max(s.session_date for s in sessions)
        return ParityReport(
            strategy=sessions[0].strategy,
            behaviour_hash=sessions[0].behaviour_hash,
            kind=kind,
            first_session=first,
            last_session=last,
            verdict=verdict.verdict,
            gates=tuple(GateFinding(g.name, g.outcome.value, g.detail) for g in verdict.gates),
            sessions=metrics.sessions,
            matched_trades=metrics.matched_trades,
            metrics=self._table.of(metrics),
            recorded_at=self._clock.now(),
            payload=payload or {},
        )

    def _alert_if_rejected(self, report: ParityReport) -> None:
        if report.verdict is Verdict.REJECTED:
            failing = "; ".join(f"{g.name}: {g.detail}" for g in report.failing)
            self._alerts.raise_alert(
                f"parity_degraded:{report.strategy}",
                f"{report.kind.value} {report.period}: {failing}",
            )
