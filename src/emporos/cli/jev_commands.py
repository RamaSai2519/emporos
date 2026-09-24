"""`emporos backtest jev-compare` — does Jev add anything to a portfolio that already works?

The same portfolio is run twice over identical data, costs, capital and holdout: once without Jev
and once with it (each declared mode and threshold), and the result is published as a
`jev_incremental` experiment report. The expected outcome today is "Jev is not eligible to lift
anything": no strategy in the plan has a VALIDATED baseline, and the policy will not let Jev
rescue one that has not validated on its own.

Two provider modes. `replay` (the default) answers only from the recorded journal, costs nothing
and reproduces a published report. `record` asks the live model for the questions the journal does
not hold; it needs `VERCEL_GATEWAY_KEY`, prints its worst-case token and rupee ceiling first,
enforces a hard cap on live calls, and asks for confirmation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import typer
from pydantic import ValidationError

from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.fingerprint import FingerprintContext
from emporos.backtest.integrity import ResearchIntegrityGate
from emporos.backtest.jev_baseline import BaselineVerdictFolder
from emporos.backtest.jev_incremental import JevIncrementalAnalysis
from emporos.backtest.jev_report import JevExperimentReportBuilder, JevProvenance, JevReportSource
from emporos.backtest.jev_sweep import JevSweep, JevSweepOutcome, JevSweepRequest, JevVariant
from emporos.backtest.job import ResolverTickSizes
from emporos.backtest.multi_engine import MultiStrategyBacktestSpec
from emporos.backtest.provenance import ProvenanceSnapshotter
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.robustness.benchmark import (
    DEFAULT_BENCHMARK_FILE,
    BenchmarkLoader,
    BenchmarkScaler,
)
from emporos.backtest.robustness.jev_gate import JevIncrementalPolicy
from emporos.backtest.robustness.paired_bootstrap import PairedDayBootstrap
from emporos.backtest.robustness.trials import InMemoryTrialLedger, TrialLedger
from emporos.cli.backtest_runtime import BacktestRuntime, open_backtest_runtime
from emporos.cli.curation_commands import PlanCostModel
from emporos.cli.experiment_declarations import DeclarationGate, ExperimentDeclarationLoader
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.experiment_registry import (
    DEFAULT_EXPERIMENTS_DIR,
    ExperimentPublication,
    FileExperimentRegistry,
)
from emporos.cli.jev_composition import (
    DEFAULT_PLAN,
    BaselineStandings,
    HoldoutSplit,
    JevPlanLoader,
    JevProviderStack,
    MultiStrategyEngineFactory,
    TradingDayWindow,
)
from emporos.cli.strategy_composition import build_registry
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.money import Money
from emporos.domain.research_experiments import (
    CostModelVersion,
    DatasetVersion,
    DatePair,
    ExperimentDeclaration,
    VersionStamp,
)
from emporos.domain.verdicts import RecordedVerdict
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.jev.budget import CallBudgetJevProvider, JevSpendCeiling
from emporos.jev.client import VercelGatewayJevClient
from emporos.jev.config import JevConfig, require_credentials
from emporos.jev.declaration import JevExperimentDeclaration
from emporos.jev.leakage import DEFAULT_CUTOFF_MARGIN, KnowledgeCutoffGuard
from emporos.jev.prompts import DEFAULT_PROMPT
from emporos.jev.protocol import JevProvider
from emporos.jev.replay import JevDecisionJournal
from emporos.marketdata.session import SessionWindow
from emporos.persistence.jev_journal import MongoJevDecisionJournal
from emporos.persistence.trial_ledger import MongoTrialLedger
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.snapshot import ConfigSnapshotter

# An estimate, printed as one: a system prompt plus a compact JSON context is a few hundred tokens
# and the reply is a short JSON object. The real per-call count comes back and is what is reported.
ESTIMATED_TOKENS_PER_CALL = 400
DEFAULT_MAX_REQUESTS = 500
BOOTSTRAP_SEED = 20260924


class ProviderMode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"


_DECLARATION = typer.Option(
    ...,
    exists=True,
    dir_okay=False,
    help="Experiment declaration (config/experiments/<slug>.yaml, family jev_incremental), "
    "committed BEFORE the run: it fixes the model, its knowledge cutoff and source, the token "
    "price, the prompt and the modes and thresholds that may be tried.",
)
_PLAN = typer.Option(DEFAULT_PLAN, exists=True, dir_okay=False, help="The portfolio to compare.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First trading day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last trading day (inclusive).")
_MODE = typer.Option(
    None, "--mode", help="A declared mode to run (repeatable); default: every declared mode."
)
_THRESHOLD = typer.Option(
    None,
    "--threshold",
    help="A declared confidence threshold to run (repeatable); default: every declared one.",
)
_PROVIDER = typer.Option(
    ProviderMode.REPLAY,
    "--provider",
    help="replay: answer only from the recorded journal (free, reproducible). record: ask the "
    "live model for what the journal lacks (costs money; needs VERCEL_GATEWAY_KEY).",
)
_ANONYMISE = typer.Option(
    True,
    "--anonymise/--no-anonymise",
    help="Replace the symbol with a salted pseudonym before the model sees it.",
)
_MAX_REQUESTS = typer.Option(
    DEFAULT_MAX_REQUESTS,
    min=1,
    help="record only: the hard cap on live Jev calls; the run stops when it is reached.",
)
_YES = typer.Option(False, "--yes", help="record only: skip the spend confirmation.")
_BENCHMARK = typer.Option(
    DEFAULT_BENCHMARK_FILE, exists=True, dir_okay=False, help="Benchmark capital and costs."
)
_EXPERIMENTS_DIR = typer.Option(
    DEFAULT_EXPERIMENTS_DIR, help="Where published experiment reports live."
)
_UNCOMMITTED = typer.Option(
    False,
    "--allow-uncommitted-declaration",
    help="Run even though the declaration is not committed (it then proves nothing about having "
    "been declared before the run).",
)
_FEES = typer.Option(False, help="Price days before the oldest fee schedule with it.")
_UNIVERSE = typer.Option(
    False, help="Use an instrument's earliest recorded definition where the master has no history."
)
_RECORD_TRIALS = typer.Option(
    True,
    "--record-trials/--no-record-trials",
    help="Append every variant to the trial ledger (the Deflated Sharpe prices the search).",
)


@dataclass(frozen=True)
class JevCompareRequest:
    declaration: ExperimentDeclaration
    plan: Path
    first: date
    last: date
    modes: tuple[str, ...] | None
    thresholds: tuple[Decimal, ...] | None
    provider: ProviderMode
    anonymise: bool
    max_requests: int
    assume_yes: bool
    benchmark: Path
    experiments_dir: Path
    assume_earliest_fees: bool
    assume_current_universe: bool
    record_trials: bool


class SpendConfirmation:
    """The up-front spend disclosure for a recording run, and the operator's yes or no."""

    def __init__(self, ceiling: JevSpendCeiling, assume_yes: bool) -> None:
        self._ceiling = ceiling
        self._assume_yes = assume_yes

    def confirm(self) -> bool:
        c = self._ceiling
        typer.echo(
            f"record mode: at most {c.max_calls} live Jev call(s); at an ESTIMATED "
            f"{c.tokens_per_call} tokens each that is up to {c.tokens} tokens, about "
            f"{c.inr:.2f} INR at {c.inr_per_1k_tokens} INR per 1k tokens. Questions already "
            "in the journal are free."
        )
        return self._assume_yes or typer.confirm("Spend it?", default=False)


class VerdictLookup(Protocol):
    async def latest(self, strategy: str) -> RecordedVerdict | None: ...


@dataclass(frozen=True)
class JevStores:
    """What the run reads and writes besides candles: recorded verdicts, the decision journal and
    the trial ledger."""

    verdicts: VerdictLookup
    journal: JevDecisionJournal
    ledger: TrialLedger


class JevStoresFactory(Protocol):
    def open(self, database: Any, record_trials: bool) -> JevStores: ...


class MongoJevStoresFactory:
    def open(self, database: Any, record_trials: bool) -> JevStores:
        ledger: TrialLedger = InMemoryTrialLedger()
        if record_trials:
            ledger = MongoTrialLedger(database)
        return JevStores(MongoVerdictBook(database), MongoJevDecisionJournal(database), ledger)


RuntimeOpener = Callable[[Settings], AbstractAsyncContextManager[BacktestRuntime]]
LiveProviderFactory = Callable[[JevConfig, str], JevProvider]


def _vercel_client(config: JevConfig, key: str) -> JevProvider:
    return VercelGatewayJevClient(config, key)


class JevCompareRun:
    def __init__(
        self,
        settings: Settings,
        runtime: RuntimeOpener = open_backtest_runtime,
        stores: JevStoresFactory | None = None,
        live: LiveProviderFactory = _vercel_client,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._stores = stores or MongoJevStoresFactory()
        self._live = live

    async def run(
        self, request: JevCompareRequest
    ) -> tuple[JevSweepOutcome, JevReportSource, VersionStamp]:
        jev = JevExperimentDeclaration.from_declaration(request.declaration)
        self._check_prompt(jev)
        modes, thresholds = jev.select(request.modes, request.thresholds)
        variants = tuple(JevVariant(m, t) for m in modes for t in thresholds)
        config = jev.model.apply_to(
            JevConfig(enabled=True, inr_per_1k_tokens=jev.inr_per_1k_tokens, max_retries=1)
        )
        guard = KnowledgeCutoffGuard()
        guard.check(request.first, config)  # before any I/O: a leaking window costs nothing
        plan = JevPlanLoader().load(request.plan)
        benchmark = BenchmarkLoader(request.benchmark).load()
        scaler = BenchmarkScaler(benchmark)
        split = HoldoutSplit().split(request.first, request.last, plan.holdout_days)
        capital = Money(benchmark.capital)
        live = self._live_provider(config, request, jev)
        async with self._runtime(self._settings) as runtime:
            registry = build_registry()
            universe = runtime.instruments.as_of(
                SessionWindow().open_at(split.first),
                assume_earliest_before_history=request.assume_current_universe,
            )
            loader = StrategyConfigLoader(StrategyConfigResolver(registry, universe.resolver))
            files = {path: loader.load_file(path) for path in plan.strategy_files}
            configs = {c.name: c for c in files.values()}
            scaled = [scaler.strategy(c) for c in files.values()]
            self._check_integrity(runtime.quarantine, scaled, split.first, split.last)
            provenance = ProvenanceSnapshotter().take(
                universe,
                sorted({i for c in scaled for i in c.instrument_ids}),
                scaled[0].timeframe,
                split.first,
                split.last,
                runtime.quarantine,
                runtime.calendar.content_hash(),
            )
            library = FeeScheduleLibrary.from_directory()
            schedule = PlanCostModel.schedule(library, split.last)
            stores = self._stores.open(runtime.database, request.record_trials)
            recorded = {n: await stores.verdicts.latest(n) for n in configs}
            standings = BaselineStandings(recorded.get).of(configs)
            baseline_verdict = BaselineVerdictFolder().fold(standings)
            limits = scaler.limits(RiskLimitsLoader().load())

            def schedules() -> ScheduleSource:
                if request.assume_earliest_fees:
                    return EarliestBeforeFirst(library)
                return StrictSchedules(library)

            factory = MultiStrategyEngineFactory(
                runtime.reader,
                registry,
                ResolverTickSizes(universe.resolver),
                schedules,
                RiskGateFactory(limits),
            )
            experiment_id = str(ExperimentIdMinter().mint(request.declaration))
            stack = JevProviderStack(
                stores.journal, DEFAULT_PROMPT, config.model, experiment_id, request.anonymise
            )
            provider = stack.replay() if live is None else stack.record(live, variants[0].mode)
            sweep = JevSweep(
                factory,
                provider,
                stores.ledger,
                JevIncrementalAnalysis(PairedDayBootstrap(BOOTSTRAP_SEED)),
                JevIncrementalPolicy.standard(),
                guard,
                lambda: datetime.now(UTC),
            )
            spec = MultiStrategyBacktestSpec(
                configs=scaled,
                window=TradingDayWindow.of(split.first, split.last),
                starting_cash=capital,
                constraints=plan.constraints(capital),
                assumptions=(
                    f"the plan {request.plan} scaled to the benchmark capital "
                    f"{benchmark.capital}",
                ),
            )
            context = FingerprintContext(
                provenance=provenance, fee_schedule_id=schedule.name, holdout=split.holdout
            )
            outcome = await sweep.run(
                JevSweepRequest(
                    spec=spec,
                    context=context,
                    variants=variants,
                    config=config,
                    prompt=DEFAULT_PROMPT,
                    experiment=f"jev-{request.declaration.slug}",
                    strategy_label="+".join(sorted(configs)),
                    baseline_verdict=baseline_verdict,
                    dataset_version=f"candles {provenance.dataset_timeframe.value} "
                    f"{split.first}..{split.last}",
                    cost_model=f"Angel One fee schedule {schedule.name}",
                )
            )
        source = JevReportSource(
            outcome=outcome,
            provenance=JevProvenance(
                model=config.model,
                knowledge_cutoff=jev.model.knowledge_cutoff.isoformat(),
                cutoff_source=jev.model.source,
                cutoff_margin_days=DEFAULT_CUTOFF_MARGIN.days,
                prompt_version=DEFAULT_PROMPT.version,
                prompt_hash=DEFAULT_PROMPT.content_hash,
                provider_mode=request.provider.value,
                anonymised=request.anonymise,
            ),
            window=DatePair(split.first, split.last),
            holdout=split.holdout,
            baseline_standings={n: s.value for n, s in standings.items()},
        )
        versions = VersionStamp(
            candidate_behaviour_hashes={
                name: ConfigSnapshotter().take(config).behaviour_hash
                for name, config in configs.items()
            },
            dataset=DatasetVersion(
                provenance.universe_hash,
                provenance.calendar_version,
                provenance.quarantine_hash,
                provenance.dataset_timeframe.value,
                provenance.dataset_first,
                provenance.dataset_last,
            ),
            cost_model=CostModelVersion(schedule.name, None, None),
            code_revision=GitRepository().revision(),
        )
        return outcome, source, versions

    def _live_provider(
        self, config: JevConfig, request: JevCompareRequest, jev: JevExperimentDeclaration
    ) -> JevProvider | None:
        if request.provider is ProviderMode.REPLAY:
            return None
        key = require_credentials(config, self._settings.vercel_gateway_key)
        ceiling = JevSpendCeiling(
            request.max_requests, ESTIMATED_TOKENS_PER_CALL, jev.inr_per_1k_tokens
        )
        if not SpendConfirmation(ceiling, request.assume_yes).confirm():
            raise EmporosError("recording cancelled: nothing was spent")
        return CallBudgetJevProvider(self._live(config, key), request.max_requests)

    @staticmethod
    def _check_prompt(jev: JevExperimentDeclaration) -> None:
        if (jev.prompt_version, jev.prompt_hash) != (
            DEFAULT_PROMPT.version,
            DEFAULT_PROMPT.content_hash,
        ):
            raise EmporosError(
                f"the declared prompt ({jev.prompt_version}, {jev.prompt_hash[:12]}) is not the "
                f"one this build asks with ({DEFAULT_PROMPT.version}, "
                f"{DEFAULT_PROMPT.content_hash[:12]}): declare the prompt actually used"
            )

    @staticmethod
    def _check_integrity(
        quarantine: CorporateActionQuarantine,
        configs: list[ResolvedStrategyConfig],
        first: date,
        last: date,
    ) -> None:
        gate = ResearchIntegrityGate(quarantine)
        for config in configs:
            gate.check(config.instrument_ids, first, last, allow_quarantined=False)


def backtest_jev_compare(
    declaration: Path = _DECLARATION,
    first: datetime = _FROM,
    last: datetime = _TO,
    plan: Path = _PLAN,
    mode: list[str] | None = _MODE,
    threshold: list[str] | None = _THRESHOLD,
    provider: ProviderMode = _PROVIDER,
    anonymise: bool = _ANONYMISE,
    max_requests: int = _MAX_REQUESTS,
    yes: bool = _YES,
    benchmark: Path = _BENCHMARK,
    experiments_dir: Path = _EXPERIMENTS_DIR,
    allow_uncommitted_declaration: bool = _UNCOMMITTED,
    assume_earliest_fees: bool = _FEES,
    assume_current_universe: bool = _UNIVERSE,
    record_trials: bool = _RECORD_TRIALS,
) -> None:
    """Compare a portfolio with Jev off and on, and publish the result as a Jev experiment."""
    try:
        declared = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            declaration, allow_uncommitted=allow_uncommitted_declaration
        )
        request = JevCompareRequest(
            declaration=declared,
            plan=plan,
            first=first.date(),
            last=last.date(),
            modes=tuple(mode) if mode else None,
            thresholds=tuple(Decimal(t) for t in threshold) if threshold else None,
            provider=provider,
            anonymise=anonymise,
            max_requests=max_requests,
            assume_yes=yes,
            benchmark=benchmark,
            experiments_dir=experiments_dir,
            assume_earliest_fees=assume_earliest_fees,
            assume_current_universe=assume_current_universe,
            record_trials=record_trials,
        )
        runner = JevCompareRun(Settings.default())
        outcome, source, versions = asyncio.run(runner.run(request))
        report, published = ExperimentPublication(
            JevExperimentReportBuilder(), FileExperimentRegistry(experiments_dir)
        ).publish(source, declared, versions)
    except (EmporosError, ValidationError, ValueError, InvalidOperation, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"jev-compare failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for v in outcome.variants:
        e = v.evidence
        label = v.variant.candidate_label(DEFAULT_PROMPT.version)
        typer.echo(
            f"{label}: {v.verdict.verdict.value.upper()}"
            f" | net {e.deltas.net_pnl:+.2f} INR, Jev cost {e.cost.inr:.2f} INR, "
            f"net of Jev {e.net_of_jev_pnl_delta:+.2f} INR, trades {e.deltas.trade_count:+d}"
        )
    typer.echo(
        f"experiment {report.experiment_id}: {report.outcome.value.upper()} ({published.value}) "
        f"in {experiments_dir}"
    )
