"""Run every pre-declared Jev variant against one baseline and judge each (EM-187).

    baseline (Jev off, run ONCE) ──┐
    variant 1 (mode, threshold) ───┼─▶ verified against one fingerprint ─▶ comparison
    variant 2 ...                  ┘
              │
              ▼  every variant is appended to the trial ledger BEFORE any is analysed, so the
                 Deflated Sharpe of each arm counts the whole search, not just the winner
              ▼
    evidence (JevIncrementalAnalysis) ─▶ verdict (JevIncrementalPolicy) per variant

Leakage is refused up front (`KnowledgeCutoffGuard` on the window), and a run in which the model
or the journal failed to answer any question is refused rather than reported: a failed review is
treated by the filter as a rejection, so such a run would measure the outage, not Jev.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from emporos.backtest.fingerprint import (
    ExperimentFingerprint,
    ExperimentFingerprinter,
    FingerprintContext,
)
from emporos.backtest.jev_incremental import JevIncrementalAnalysis, JevIncrementalEvidence
from emporos.backtest.jev_pnl import ArmVerification, EngineFactory, JevPnlComparison
from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.multi_engine import MultiStrategyBacktestSpec
from emporos.backtest.robustness.jev_gate import JevIncrementalPolicy
from emporos.backtest.robustness.trials import TrialLedger, TrialStatistics
from emporos.backtest.robustness.verdict import VerdictReport
from emporos.core.clock import IST
from emporos.core.errors import DefinitiveError
from emporos.domain.experiments import DuplicateTrialError, Trial, TrialRole
from emporos.jev.config import JevConfig
from emporos.jev.leakage import CutoffEnforcingJevProvider, KnowledgeCutoffGuard
from emporos.jev.models import RANKING, STRATEGY_SELECTION
from emporos.jev.prompts import JevPrompt
from emporos.jev.protocol import JevProvider
from emporos.jev.tally import JevOutcomeTally, TallyingJevProvider
from emporos.opportunity.jev_filter import JevMetaDecisionFilter


class JevIncompleteRun(DefinitiveError):
    """The model or the journal did not answer every question, so the run measures the outage."""


@dataclass(frozen=True)
class JevVariant:
    """One arm of the search: a mode and a confidence threshold."""

    mode: str
    threshold: Decimal

    @property
    def arm(self) -> str:
        """STRATEGY_SELECTION is the same ask as RANKING (see `jev_filter`), so it is one arm."""
        return RANKING if self.mode == STRATEGY_SELECTION else self.mode

    def candidate(self, prompt: JevPrompt) -> str:
        return self.candidate_label(prompt.version)

    def candidate_label(self, prompt_version: str) -> str:
        threshold = format(self.threshold.normalize(), "f")
        return f"jev:{self.arm}:{threshold}:{prompt_version}"


@dataclass(frozen=True)
class JevSweepRequest:
    spec: MultiStrategyBacktestSpec
    context: FingerprintContext
    variants: tuple[JevVariant, ...]
    config: JevConfig  # the declared model, cutoff and INR rate; mode and threshold vary per arm
    prompt: JevPrompt
    experiment: str  # the trial ledger's research programme, e.g. "jev-<slug>"
    strategy_label: str  # the portfolio judged, e.g. "momentum_v1+orb_v1"
    baseline_verdict: VerdictReport
    dataset_version: str
    cost_model: str

    def __post_init__(self) -> None:
        arms = [v.candidate(self.prompt) for v in self.variants]
        if not arms:
            raise ValueError("a Jev sweep needs at least one variant")
        if len(set(arms)) != len(arms):
            raise ValueError("a Jev sweep's variants must be distinct arms")


@dataclass(frozen=True)
class JevVariantOutcome:
    variant: JevVariant
    comparison: JevPnlComparison
    evidence: JevIncrementalEvidence
    verdict: VerdictReport
    tally: JevOutcomeTally


@dataclass(frozen=True)
class JevSweepOutcome:
    request: JevSweepRequest
    fingerprint: ExperimentFingerprint
    variants: tuple[JevVariantOutcome, ...]
    trial_count: int  # the ledger's size when the arms were analysed: what the DSR was priced at

    @property
    def headline(self) -> JevVariantOutcome:
        """The arm with the largest per-trade gain net of Jev's cost (the first on a tie): chosen
        by outcome, which is why every arm is a recorded trial and the holdout must confirm."""

        def gain(outcome: JevVariantOutcome) -> Decimal:
            value = outcome.evidence.net_of_jev_average_trade_delta
            return value if value is not None else Decimal("-Infinity")

        return max(self.variants, key=gain)


class JevSweep:
    def __init__(
        self,
        factory: EngineFactory,
        provider: JevProvider,
        ledger: TrialLedger,
        analysis: JevIncrementalAnalysis,
        policy: JevIncrementalPolicy,
        guard: KnowledgeCutoffGuard,
        now: Callable[[], datetime],
        fingerprinter: ExperimentFingerprinter | None = None,
    ) -> None:
        self._factory = factory
        self._provider = provider
        self._ledger = ledger
        self._analysis = analysis
        self._policy = policy
        self._guard = guard
        self._now = now
        self._fingerprinter = fingerprinter or ExperimentFingerprinter()

    async def run(self, request: JevSweepRequest) -> JevSweepOutcome:
        self._guard.check(request.spec.window.start.astimezone(IST).date(), request.config)
        fingerprint = self._fingerprinter.fingerprint(request.spec, request.context)
        baseline = await self._factory.build(None).run(request.spec)
        ran: list[tuple[JevVariant, JevPnlComparison, JevOutcomeTally]] = []
        for variant in request.variants:
            tally = JevOutcomeTally()
            treatment = await self._factory.build(self._filter(request, variant, tally)).run(
                request.spec
            )
            if not tally.complete:
                raise JevIncompleteRun(
                    f"{variant.candidate(request.prompt)}: {tally.failures} of {tally.requests} "
                    f"Jev request(s) failed ({tally.not_recorded} not in the journal): "
                    "record the missing decisions first"
                )
            ArmVerification().check(fingerprint, request.context, baseline, treatment)
            ran.append((variant, JevPnlComparison(baseline, treatment, fingerprint), tally))
        await self._record_trials(request, ran, fingerprint)
        statistics = TrialStatistics.of(await self._ledger.all())
        outcomes = tuple(self._judge(request, statistics, *entry) for entry in ran)
        return JevSweepOutcome(request, fingerprint, outcomes, statistics.count)

    def _filter(
        self, request: JevSweepRequest, variant: JevVariant, tally: JevOutcomeTally
    ) -> JevMetaDecisionFilter:
        config = replace(
            request.config,
            enabled=True,
            mode=variant.mode,
            confidence_threshold=float(variant.threshold),
            fail_open=False,  # a failed review blocks the candidate, exactly as in live trading
        )
        guarded = CutoffEnforcingJevProvider(
            TallyingJevProvider(self._provider, tally), self._guard, request.config
        )
        return JevMetaDecisionFilter(guarded, config)

    def _judge(
        self,
        request: JevSweepRequest,
        statistics: TrialStatistics,
        variant: JevVariant,
        comparison: JevPnlComparison,
        tally: JevOutcomeTally,
    ) -> JevVariantOutcome:
        evidence = self._analysis.of(comparison, statistics, request.config.inr_per_1k_tokens)
        # The Jev arm is not judged by the full walk-forward policy here (a single fill-level pass
        # has no windows to walk), so it is passed as unjudged and can never reach VALIDATED.
        verdict = self._policy.classify(request.baseline_verdict, None, evidence)
        return JevVariantOutcome(variant, comparison, evidence, verdict, tally)

    async def _record_trials(
        self,
        request: JevSweepRequest,
        ran: Sequence[tuple[JevVariant, JevPnlComparison, JevOutcomeTally]],
        fingerprint: ExperimentFingerprint,
    ) -> None:
        now = self._now()
        for variant, comparison, _ in ran:
            metrics = comparison.treatment.metrics
            root_days = DecimalMath.sqrt(Decimal(metrics.settings.annualisation_days))
            sharpe = metrics.returns.sharpe
            candidate = variant.candidate(request.prompt)
            trial = Trial(
                trial_id=self._trial_id(request, fingerprint, candidate),
                experiment=request.experiment,
                strategy=request.strategy_label,
                candidate=candidate,
                role=TrialRole.TEST,
                dataset_version=request.dataset_version,
                config_hash=fingerprint.digest,
                cost_model=request.cost_model,
                recorded_at=now,
                trade_count=metrics.trades.count,
                net_pnl=metrics.trades.net_pnl.amount,
                daily_sharpe=None if sharpe is None else DecimalMath.divide(sharpe, root_days),
                note=f"Jev {variant.mode} at threshold {variant.threshold}, "
                f"prompt {request.prompt.version}",
            )
            try:
                await self._ledger.append(trial)
            except DuplicateTrialError:
                continue  # replaying the same experiment is the same trials, not new ones

    @staticmethod
    def _trial_id(
        request: JevSweepRequest, fingerprint: ExperimentFingerprint, candidate: str
    ) -> str:
        """Deterministic: the same experiment, assumptions and arm is the same trial, so a replay
        that reproduces a published report does not enlarge the search it is reproducing."""
        digest = hashlib.sha256(
            f"{request.experiment}|{fingerprint.digest}|{candidate}".encode()
        ).hexdigest()
        return f"jev-{digest[:24]}"
