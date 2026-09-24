"""The graduation console: what the operator sees, and that only the typed phrase acknowledges."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.cli.graduation_console import GraduationConsole, StrategySubjects
from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.experiments import Verdict
from emporos.domain.graduation import GraduationStage, acknowledgement_phrase
from emporos.graduation.policy import standard_policy
from emporos.graduation.requirements import (
    BrokerVerificationPassed,
    HoldoutEvaluated,
    LiveAcknowledged,
    PaperReconciliationPassed,
    ValidatedVerdictForConfig,
)
from emporos.graduation.service import GraduationService
from emporos.graduation.unverified import UnverifiedBrokerEvidence
from emporos.risk.config import RiskLimitsLoader, RiskTier
from tests.support.graduation import (
    HASH,
    NOW,
    STRATEGY,
    FakeExperiments,
    FakeParity,
    FakeVerdictBook,
    MemoryAcknowledgements,
    MemoryLedger,
    experiment_view,
    parity_report,
    recorded,
)

S = GraduationStage
EXP = "EXP-20260924-orb-v1-abcd1234"


class Typed:
    """A person at the keyboard: says what they were told to say, and remembers being asked."""

    def __init__(self, answer: str) -> None:
        self.answer, self.asked = answer, []

    def __call__(self, question: str) -> str:
        self.asked.append(question)
        return self.answer


def console(verdict: Verdict = Verdict.VALIDATED):  # type: ignore[no-untyped-def]
    ledger, book = MemoryLedger(), MemoryAcknowledgements()
    experiments = FakeExperiments(experiment_view())
    verdicts = FakeVerdictBook(recorded(verdict))
    clock = FixedClock(NOW)
    paper = [ValidatedVerdictForConfig(verdicts), HoldoutEvaluated(experiments)]
    live = [
        PaperReconciliationPassed(FakeParity(parity_report()), 10),
        BrokerVerificationPassed(UnverifiedBrokerEvidence(), clock, timedelta(days=7)),
        LiveAcknowledged(book),
    ]
    service = GraduationService(ledger, standard_policy(paper, live), clock)
    limits = RiskLimitsLoader.for_tier(RiskTier.LIVE_CONSERVATIVE).load()
    return (
        GraduationConsole(
            service,
            StrategySubjects({STRATEGY: HASH, "other_v1": "1" * 16}),
            book,
            experiments,
            verdicts,
            clock,
            RiskTier.LIVE_CONSERVATIVE,
            limits,
        ),  # fmt: skip
        ledger,
        book,
    )


class TestSubjects:
    def test_an_unknown_strategy_is_a_configuration_error_naming_the_known_ones(self) -> None:
        with pytest.raises(ConfigurationError, match="known: orb_v1, other_v1"):
            StrategySubjects({STRATEGY: HASH, "other_v1": "1"}).hash_of("nope")


class TestPromote:
    async def test_a_promotion_with_met_requirements_is_recorded(self) -> None:
        cons, ledger, _ = console()
        out = await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        assert out.ok and "research->paper" in out.lines[0]
        assert len(ledger.events) == 1

    async def test_every_unmet_requirement_is_printed_and_nothing_is_recorded(self) -> None:
        cons, ledger, _ = console(Verdict.REJECTED)
        out = await cons.promote(STRATEGY, S.PAPER, "rama", None)
        assert not out.ok and out.lines[0] == "orb_v1: NOT promoted to paper"
        assert any("rejected" in line for line in out.lines)
        assert any("no experiment report is cited" in line for line in out.lines)
        assert ledger.events == []

    async def test_going_live_lists_the_fail_closed_broker_evidence_by_check(self) -> None:
        cons, _, _ = console()
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        out = await cons.promote(STRATEGY, S.LIVE_CONSERVATIVE, "rama", EXP)
        assert not out.ok
        text = "\n".join(out.lines)
        assert "static_ip_registered: unknown" in text and "login_and_session: unknown" in text
        assert "no human acknowledgement" in text

    async def test_a_lost_race_is_reported_not_raised(self) -> None:
        cons, ledger, _ = console()

        async def stale(strategy: str):  # type: ignore[no-untyped-def]
            return None

        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        ledger.latest = stale  # type: ignore[method-assign]
        out = await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        assert not out.ok and "another promotion won the race" in out.lines[0]


class TestStatusHistoryDemote:
    async def test_status_shows_each_requirement_of_the_next_stage(self) -> None:
        cons, _, _ = console()
        out = await cons.status([STRATEGY])
        assert out.lines[0] == "orb_v1 @ abcdef01: research -> next: paper"
        assert any("[ok  ] validated_verdict_for_config" in line for line in out.lines)
        assert any("[MISS] holdout_evaluated" in line for line in out.lines)  # nothing cited

    async def test_status_without_names_covers_every_strategy(self) -> None:
        cons, _, _ = console()
        out = await cons.status([])
        assert len([line for line in out.lines if "-> next:" in line]) == 2

    async def test_history_and_demote(self) -> None:
        cons, _, _ = console()
        assert (await cons.history(STRATEGY)).lines == ("orb_v1: no graduation events",)
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        demoted = await cons.demote(STRATEGY, S.RESEARCH, "rama", "drift")
        assert demoted.ok
        lines = (await cons.history(STRATEGY)).lines
        assert len(lines) == 2 and "demote paper->research by rama: drift" in lines[1]

    async def test_an_invalid_demotion_is_reported(self) -> None:
        cons, _, _ = console()
        out = await cons.demote(STRATEGY, S.PAPER, "rama", "x")
        assert not out.ok and "cannot be demoted" in out.lines[0]

    async def test_a_strategy_at_paper_shows_live_as_the_next_stage(self) -> None:
        cons, _, _ = console()
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        out = await cons.status([STRATEGY])
        assert "next: live_conservative" in out.lines[0]


class TestAcknowledge:
    PHRASE = acknowledgement_phrase(STRATEGY, HASH)

    async def test_it_refuses_a_strategy_that_has_not_reached_paper(self) -> None:
        cons, _, book = console()
        typed = Typed(self.PHRASE)
        out = await cons.acknowledge(STRATEGY, "rama", typed)
        assert not out.ok and "promote it to paper before" in out.lines[0]
        assert typed.asked == [] and await book.get(STRATEGY, HASH) is None

    async def test_the_exact_phrase_records_the_acknowledgement_after_showing_what_it_means(
        self,
    ) -> None:
        cons, _, book = console()
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        typed = Typed(self.PHRASE)

        out = await cons.acknowledge(STRATEGY, "rama", typed, EXP)

        assert out.ok and out.lines[-1] == "Acknowledged by rama."
        text = "\n".join(out.lines)
        assert "risk tier:            live_conservative" in text
        assert "account capital:      50000" in text and "max risk per trade:   150" in text
        assert f"experiment:           {EXP} (accepted, holdout reserved)" in text
        assert "verdict:              validated" in text
        assert typed.asked == [f"Type {self.PHRASE!r} exactly to accept the first live deployment"]
        recorded_ack = await book.get(STRATEGY, HASH)
        assert recorded_ack is not None and recorded_ack.risk_tier == "live_conservative"
        assert recorded_ack.operator == "rama" and recorded_ack.at == NOW

    @pytest.mark.parametrize(
        "typed", ["", "yes", "orb_v1@abcdef01 live", "orb_v1@abcdef01 LIVE ", "LIVE"]
    )
    async def test_any_other_text_records_nothing(self, typed: str) -> None:
        cons, _, book = console()
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)

        out = await cons.acknowledge(STRATEGY, "rama", Typed(typed))

        assert not out.ok and "nothing was recorded" in out.lines[-1]
        assert await book.get(STRATEGY, HASH) is None

    async def test_a_second_acknowledgement_is_refused_not_stacked(self) -> None:
        cons, _, _ = console()
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        await cons.acknowledge(STRATEGY, "rama", Typed(self.PHRASE))
        out = await cons.acknowledge(STRATEGY, "rama", Typed(self.PHRASE))
        assert not out.ok and "already acknowledged" in out.lines[-1]

    async def test_it_says_when_no_experiment_or_verdict_exists(self) -> None:
        cons, _, _ = console()
        cons._experiments = FakeExperiments()  # type: ignore[attr-defined]
        cons._verdicts = FakeVerdictBook(None)  # type: ignore[attr-defined]
        await cons.promote(STRATEGY, S.PAPER, "rama", EXP)
        # the service's own bindings still hold the evidence; only the summary is empty
        out = await cons.acknowledge(STRATEGY, "rama", Typed("no"))
        text = "\n".join(out.lines)
        assert "none published for this configuration" in text and "none recorded" in text


def test_the_shipped_conservative_tier_is_what_the_operator_is_shown() -> None:
    limits = RiskLimitsLoader.for_tier(RiskTier.LIVE_CONSERVATIVE).load()
    assert limits.max_capital_deployed == Decimal("10000")
