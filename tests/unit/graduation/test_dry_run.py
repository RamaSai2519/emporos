"""EM-189 step 9: the whole road, dry-run over scratch ids and an in-memory ledger.

No real strategy is promoted and nothing is written to Atlas: the strategy is `em189-dryrun`, the
ledger and acknowledgement book are in memory, and the acknowledgement is recorded through the
console with an injected prompt that stands in for a person (never a real operator, never real
data).
The point is to prove, with the REAL requirement classes and the REAL fail-closed broker binding,
that LIVE_CONSERVATIVE is refused with the exact reasons listed.
"""

from __future__ import annotations

from datetime import timedelta

from emporos.cli.graduation_console import GraduationConsole, StrategySubjects
from emporos.core.clock import FixedClock
from emporos.domain.broker_verification import CRITICAL_BROKER_CHECKS
from emporos.domain.experiments import Verdict
from emporos.domain.graduation import GraduationStage, acknowledgement_phrase
from emporos.graduation.policy import standard_policy
from emporos.graduation.requirements import (
    BrokerVerificationPassed,
    DataIntegrityClean,
    HoldoutEvaluated,
    JevDependencyAllowed,
    LiveAcknowledged,
    NoOpenAnomalies,
    PaperReconciliationPassed,
    PromotionRequirement,
    ValidatedVerdictForConfig,
)
from emporos.graduation.service import GraduationService
from emporos.graduation.unverified import UnverifiedBrokerEvidence
from emporos.graduation.views import BookAcknowledgementView, LedgerStageView
from emporos.risk.config import RiskLimitsLoader, RiskTier
from emporos.session.launch_gate import (
    LaunchRefused,
    LaunchRequest,
    LiveGraduation,
    live_policy,
    paper_policy,
)
from tests.support.graduation import (
    NOW,
    FakeExperiments,
    FakeParity,
    FakeQuarantine,
    FakeSwitch,
    FakeUnresolved,
    FakeVerdictBook,
    MemoryAcknowledgements,
    MemoryLedger,
    experiment_view,
    parity_report,
    recorded,
)

SCRATCH = "em189-dryrun"
HASH = "sha256:00112233445566778899"
EXP = "EXP-20260924-em189-dryrun-abcd1234"
S = GraduationStage


def rig(verdict: Verdict, *, everything_else_green: bool):  # type: ignore[no-untyped-def]
    """The production policy shape over in-memory stores. Only the broker evidence is the real
    (fail-closed) binding; the rest are green or red as the scenario needs."""
    ledger, book, clock = MemoryLedger(), MemoryAcknowledgements(), FixedClock(NOW)
    verdicts = FakeVerdictBook(recorded(verdict, HASH))
    experiments = FakeExperiments(experiment_view(EXP, hashes=frozenset({HASH})))
    parity = FakeParity(parity_report() if everything_else_green else None)
    paper: list[PromotionRequirement] = [
        ValidatedVerdictForConfig(verdicts),
        HoldoutEvaluated(experiments),
        DataIntegrityClean(experiments, FakeQuarantine()),
    ]
    live: list[PromotionRequirement] = [
        PaperReconciliationPassed(parity, 10),
        BrokerVerificationPassed(UnverifiedBrokerEvidence(), clock, timedelta(days=7)),
        LiveAcknowledged(book),
        NoOpenAnomalies(FakeSwitch(False), FakeUnresolved(0)),
        JevDependencyAllowed(experiments),
    ]
    service = GraduationService(ledger, standard_policy(paper, live), clock)
    console = GraduationConsole(
        service, StrategySubjects({SCRATCH: HASH}), book, experiments, verdicts, clock,
        RiskTier.LIVE_CONSERVATIVE, RiskLimitsLoader.for_tier(RiskTier.LIVE_CONSERVATIVE).load(),
    )  # fmt: skip
    graduation = LiveGraduation(
        LedgerStageView(ledger), BookAcknowledgementView(book), RiskTier.LIVE_CONSERVATIVE
    )
    return console, ledger, book, graduation, verdicts


class Typed:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def __call__(self, question: str) -> str:
        return self.answer


async def test_a_rejected_strategy_may_run_in_paper_only_by_naming_its_standing() -> None:
    console, _, _, graduation, verdicts = rig(Verdict.REJECTED, everything_else_green=True)

    refused = await console.promote(SCRATCH, S.PAPER, "dry-run", EXP)
    assert not refused.ok and f"  - {SCRATCH} is rejected, not validated" in refused.lines

    request = LaunchRequest(SCRATCH, HASH, True)
    policy = paper_policy(verdicts, graduation.stages)
    try:
        await policy.require(request)
    except LaunchRefused as launch:
        assert "'rejected'" in str(launch)
    await policy.require(LaunchRequest(SCRATCH, HASH, True, acknowledged="rejected"))


async def test_live_conservative_is_refused_with_the_exact_reasons_listed() -> None:
    """Everything a paper run can supply is supplied; only the human's acknowledgement and the
    broker verification (EM-186, nothing recorded) are missing, and each is named."""
    console, ledger, _, _, _ = rig(Verdict.VALIDATED, everything_else_green=True)
    assert (await console.promote(SCRATCH, S.PAPER, "dry-run", EXP)).ok

    out = await console.promote(SCRATCH, S.LIVE_CONSERVATIVE, "dry-run", EXP)

    assert not out.ok
    assert out.lines[0] == f"{SCRATCH}: NOT promoted to live_conservative"
    reasons = [line.removeprefix("  - ") for line in out.lines[1:]]
    broker = [f"{name}: unknown" for name in CRITICAL_BROKER_CHECKS]
    assert len(reasons) == 2  # the broker evidence, and the acknowledgement: nothing else
    assert reasons[0] == "broker verification not passed: " + "; ".join(broker)
    assert reasons[1].startswith("no human acknowledgement for this configuration; run")
    assert f"{SCRATCH}@00112233 LIVE" in reasons[1]
    assert [e.to_stage for e in ledger.events] == [S.PAPER]  # nothing beyond paper was recorded


async def test_even_with_a_typed_acknowledgement_the_broker_evidence_still_blocks_live() -> None:
    console, ledger, book, graduation, verdicts = rig(Verdict.VALIDATED, everything_else_green=True)
    await console.promote(SCRATCH, S.PAPER, "dry-run", EXP)
    phrase = acknowledgement_phrase(SCRATCH, HASH)
    acknowledged = await console.acknowledge(SCRATCH, "dry-run", Typed(phrase), EXP)
    assert acknowledged.ok and await book.get(SCRATCH, HASH) is not None

    out = await console.promote(SCRATCH, S.LIVE_CONSERVATIVE, "dry-run", EXP)

    assert not out.ok
    text = "\n".join(out.lines)
    assert all(f"{name}: unknown" in text for name in CRITICAL_BROKER_CHECKS)
    assert "no human acknowledgement" not in text  # that one is now met
    assert len(ledger.events) == 1

    # and the launch gate agrees: the strategy is at paper, so a live start is refused too
    policy = live_policy(verdicts, True, FakeSwitch(False), graduation)
    try:
        await policy.require(LaunchRequest(SCRATCH, HASH, True))
    except LaunchRefused as launch:
        assert any("is at paper for this configuration" in r for r in launch.reasons)
    else:  # pragma: no cover
        raise AssertionError("a strategy at paper must not be allowed live")


async def test_a_rejected_unproven_strategy_lists_every_missing_piece_at_once() -> None:
    console, _, _, _, _ = rig(Verdict.REJECTED, everything_else_green=False)

    out = await console.promote(SCRATCH, S.LIVE_CONSERVATIVE, "dry-run", None)

    # skipping paper is refused outright, by ordering, before any evidence is weighed
    assert not out.ok and "one stage at a time" in out.lines[1]
    status = await console.status([SCRATCH])
    assert any("[MISS] validated_verdict_for_config" in line for line in status.lines)
