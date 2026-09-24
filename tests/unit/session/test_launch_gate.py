"""The rules for starting a strategy. Live is off by default and cannot be argued past; paper may
run a strategy that has not earned it, but only when the operator names its standing."""

from __future__ import annotations

import pytest

from emporos.domain.experiments import Verdict
from emporos.domain.graduation import GraduationStage
from emporos.domain.verdicts import RecordedVerdict
from emporos.risk.config import RiskTier
from emporos.session.launch_gate import (
    AnyOf,
    ConfigLaunchFacts,
    LaunchRefused,
    LaunchRequest,
    PolicyStartGate,
    live_policy,
    paper_policy,
)
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.snapshot import ConfigSnapshotter
from tests.support.graduation import FakeStages, live_graduation
from tests.support.strategies import INSTRUMENT_MASTER, changed, raw_config, threshold_registry
from tests.unit.domain.test_verdicts import HASH, recorded


def shipped(enabled: bool = True) -> ResolvedStrategyConfig:
    return StrategyConfigResolver(threshold_registry(), INSTRUMENT_MASTER).resolve(
        changed(raw_config(), "enabled", enabled)
    )


class Book:
    def __init__(self, verdict: RecordedVerdict | None = None) -> None:
        self._verdict = verdict

    async def latest(self, strategy: str) -> RecordedVerdict | None:
        return self._verdict


class Switch:
    def __init__(self, halted: bool = False) -> None:
        self._halted = halted

    async def halted(self) -> bool:
        return self._halted


def request(acknowledged: str | None = None, enabled: bool = True) -> LaunchRequest:
    return LaunchRequest("orb_v1", HASH, enabled, acknowledged)


class TestPaper:
    async def test_a_validated_strategy_starts_without_ceremony(self) -> None:
        await paper_policy(
            Book(recorded(Verdict.VALIDATED)), FakeStages(GraduationStage.RESEARCH)
        ).require(request())

    @pytest.mark.parametrize(
        ("book", "word"),
        [
            (Book(recorded(Verdict.REJECTED)), "rejected"),
            (Book(recorded(Verdict.INCONCLUSIVE)), "inconclusive"),
            (Book(recorded(Verdict.VALIDATED, "sha256:old")), "stale"),
            (Book(None), "none"),
        ],
    )
    async def test_anything_else_needs_its_standing_named(self, book: Book, word: str) -> None:
        policy = paper_policy(book, FakeStages(GraduationStage.RESEARCH))

        with pytest.raises(LaunchRefused, match=word):
            await policy.require(request())
        await policy.require(request(acknowledged=word))  # typed, so it may run

    async def test_acknowledging_the_wrong_word_is_not_an_acknowledgement(self) -> None:
        policy = paper_policy(
            Book(recorded(Verdict.REJECTED)), FakeStages(GraduationStage.RESEARCH)
        )

        with pytest.raises(LaunchRefused):
            await policy.require(request(acknowledged="validated"))
        with pytest.raises(LaunchRefused):
            await policy.require(request(acknowledged="yes"))


class TestLive:
    async def test_every_condition_met_allows_it(self) -> None:
        await live_policy(
            Book(recorded(Verdict.VALIDATED)), True, Switch(), live_graduation()
        ).require(request())

    async def test_the_defaults_refuse(self) -> None:
        """Nothing is switched on: no flag, no verdict, the strategy disabled."""
        policy = live_policy(Book(None), False, Switch(), live_graduation())

        with pytest.raises(LaunchRefused) as refused:
            await policy.require(request(enabled=False))

        assert len(refused.value.reasons) == 3  # flag, enabled, verdict: all reported together

    @pytest.mark.parametrize(
        ("verdict", "flag", "enabled", "halted", "why"),
        [
            (Verdict.VALIDATED, False, True, False, "LIVE_TRADING_ENABLED"),
            (Verdict.VALIDATED, True, False, False, "enabled: false"),
            (Verdict.VALIDATED, True, True, True, "kill switch"),
            (Verdict.REJECTED, True, True, False, "rejected, not validated"),
            (Verdict.INCONCLUSIVE, True, True, False, "inconclusive, not validated"),
        ],
    )
    async def test_each_condition_alone_refuses(
        self, verdict: Verdict, flag: bool, enabled: bool, halted: bool, why: str
    ) -> None:
        policy = live_policy(Book(recorded(verdict)), flag, Switch(halted), live_graduation())

        with pytest.raises(LaunchRefused, match=why):
            await policy.require(request(enabled=enabled))

    async def test_a_verdict_for_an_edited_config_does_not_count(self) -> None:
        policy = live_policy(
            Book(recorded(Verdict.VALIDATED, "sha256:old")), True, Switch(), live_graduation()
        )

        with pytest.raises(LaunchRefused, match="different configuration"):
            await policy.require(request())

    async def test_no_verdict_refuses(self) -> None:
        with pytest.raises(LaunchRefused, match="no recorded verdict"):
            await live_policy(Book(None), True, Switch(), live_graduation()).require(request())

    async def test_an_acknowledgement_cannot_get_past_live(self) -> None:
        policy = live_policy(Book(recorded(Verdict.REJECTED)), True, Switch(), live_graduation())

        with pytest.raises(LaunchRefused):
            await policy.require(request(acknowledged="rejected"))


class TestLiveGraduation:
    """EM-189: a live launch also needs the ledger stage, the acknowledgement and the risk tier."""

    def policy(self, graduation):  # type: ignore[no-untyped-def]
        return live_policy(Book(recorded(Verdict.VALIDATED)), True, Switch(), graduation)

    async def test_graduated_acknowledged_and_on_the_right_tier_may_go_live(self) -> None:
        await self.policy(live_graduation()).require(request())

    @pytest.mark.parametrize(
        "stage", [GraduationStage.RESEARCH, GraduationStage.PAPER, GraduationStage.RETIRED]
    )
    async def test_a_strategy_below_live_conservative_is_refused_naming_the_stage(
        self, stage: GraduationStage
    ) -> None:
        with pytest.raises(LaunchRefused) as refused:
            await self.policy(live_graduation(stage=stage)).require(request())
        assert f"is at {stage.value}" in str(refused.value)
        assert "live_conservative" in str(refused.value)

    async def test_no_acknowledgement_is_refused_with_the_command_to_run(self) -> None:
        with pytest.raises(LaunchRefused, match="graduation acknowledge orb_v1"):
            await self.policy(live_graduation(acknowledged=False)).require(request())

    async def test_the_wrong_risk_tier_is_refused(self) -> None:
        with pytest.raises(LaunchRefused, match="risk tier"):
            await self.policy(live_graduation(tier=RiskTier.STANDARD)).require(request())

    async def test_a_production_stage_needs_the_standard_tier_not_the_conservative_one(
        self,
    ) -> None:
        production = live_graduation(stage=GraduationStage.PRODUCTION)
        with pytest.raises(LaunchRefused, match="loaded live_conservative"):
            await self.policy(production).require(request())
        standard = live_graduation(stage=GraduationStage.PRODUCTION, tier=RiskTier.STANDARD)
        await self.policy(standard).require(request())

    async def test_an_unpromoted_strategy_reports_every_reason_at_once(self) -> None:
        graduation = live_graduation(stage=GraduationStage.RESEARCH, acknowledged=False)
        with pytest.raises(LaunchRefused) as refused:
            await self.policy(graduation).require(request())
        assert (
            len(refused.value.reasons) == 2
        )  # stage and acknowledgement; the tier is not at issue


class TestPaperGraduation:
    async def test_a_strategy_graduated_to_paper_starts_without_naming_its_standing(self) -> None:
        policy = paper_policy(Book(recorded(Verdict.REJECTED)), FakeStages(GraduationStage.PAPER))
        await policy.require(request())

    async def test_an_ungraduated_one_still_needs_its_standing_named_and_both_reasons_show(
        self,
    ) -> None:
        policy = paper_policy(
            Book(recorded(Verdict.REJECTED)), FakeStages(GraduationStage.RESEARCH)
        )
        with pytest.raises(LaunchRefused, match="graduated to paper.*; or .*rejected"):
            await policy.require(request())
        await policy.require(request(acknowledged="rejected"))


class TestAnyOf:
    def test_it_needs_at_least_one_condition(self) -> None:
        with pytest.raises(ValueError, match="AnyOf"):
            AnyOf([])


class TestTheStartGate:
    """The control layer's `StartGate`, answered by a policy over the loadable configs."""

    def gate(self, verdict: RecordedVerdict | None) -> PolicyStartGate:
        return PolicyStartGate(
            paper_policy(Book(verdict), FakeStages(GraduationStage.RESEARCH)),
            ConfigLaunchFacts([shipped()]),
        )

    async def test_an_unknown_strategy_is_left_to_the_host_which_fails_it_by_name(self) -> None:
        assert await self.gate(None).check("no_such_strategy", None) is None

    async def test_a_verdict_recorded_for_this_config_is_recognised(self) -> None:
        current = ConfigSnapshotter().take(shipped()).behaviour_hash

        assert (
            await self.gate(recorded(Verdict.VALIDATED, current)).check("threshold", None) is None
        )

    async def test_a_verdict_for_another_config_is_stale_and_needs_acknowledging(self) -> None:
        stale = recorded(Verdict.VALIDATED, "sha256:someone-elses")

        refusal = await self.gate(stale).check("threshold", None)

        assert refusal is not None and "'stale'" in refusal
        assert await self.gate(stale).check("threshold", "stale") is None

    async def test_enabling_a_strategy_does_not_stale_its_verdict(self) -> None:
        """Live needs `enabled: true`; that edit must not orphan the verdict it was judged on."""
        off = shipped(enabled=False)
        verdict = recorded(Verdict.VALIDATED, ConfigSnapshotter().take(off).behaviour_hash)
        on_gate = PolicyStartGate(
            paper_policy(Book(verdict), FakeStages(GraduationStage.RESEARCH)),
            ConfigLaunchFacts([shipped(enabled=True)]),
        )

        assert await on_gate.check("threshold", None) is None
