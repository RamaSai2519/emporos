"""The live launch check asks the real live gate and stops, whatever the answer: it can refuse or
say "not wired", and it has no path that starts a worker or places an order."""

from __future__ import annotations

from emporos.cli.live_launch import LiveLaunchCheck, LiveLaunchOutcome
from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import RecordedVerdict
from emporos.session.launch_gate import ConfigLaunchFacts, live_policy
from emporos.strategies.snapshot import ConfigSnapshotter
from tests.unit.domain.test_verdicts import recorded
from tests.unit.session.test_launch_gate import Book, Switch, shipped


def check(
    verdict: RecordedVerdict | None,
    *,
    flag: bool = True,
    enabled: bool = True,
    halted: bool = False,
) -> LiveLaunchCheck:
    policy = live_policy(Book(verdict), flag, Switch(halted))
    return LiveLaunchCheck(policy, ConfigLaunchFacts([shipped(enabled=enabled)]))


def validated_for_shipped() -> RecordedVerdict:
    return recorded(Verdict.VALIDATED, ConfigSnapshotter().take(shipped()).behaviour_hash)


class TestTheDefaultsRefuse:
    async def test_a_default_deployment_may_not_go_live(self) -> None:
        """No flag, the strategy disabled, no verdict: the state every checkout is in."""
        report = await check(None, flag=False, enabled=False).run(["threshold"])

        assert report.outcome is LiveLaunchOutcome.REFUSED
        assert len(report.refusals["threshold"]) == 3
        assert report.exit_code == 1

    async def test_the_reasons_are_printed_so_the_operator_can_fix_them_in_one_pass(self) -> None:
        report = await check(None, flag=False, enabled=False).run(["threshold"])

        text = "\n".join(report.lines())
        assert "LIVE_TRADING_ENABLED" in text and "enabled: false" in text
        assert "no recorded verdict" in text

    async def test_naming_no_strategy_is_refused(self) -> None:
        report = await check(validated_for_shipped()).run([])

        assert report.outcome is LiveLaunchOutcome.REFUSED

    async def test_an_unknown_strategy_is_refused_by_name(self) -> None:
        report = await check(validated_for_shipped()).run(["nope"])

        assert report.refusals["nope"] == ("unknown strategy 'nope'",)


class TestEveryConditionMet:
    async def test_it_still_starts_nothing_and_says_why(self) -> None:
        report = await check(validated_for_shipped()).run(["threshold"])

        assert report.outcome is LiveLaunchOutcome.NOT_WIRED and not report.refusals
        assert report.exit_code == 2  # never 0: a caller cannot mistake it for a started worker
        assert "not built" in report.lines()[-1]

    async def test_one_failing_strategy_among_several_refuses_the_run(self) -> None:
        report = await check(validated_for_shipped()).run(["threshold", "nope"])

        assert report.outcome is LiveLaunchOutcome.REFUSED and set(report.refusals) == {"nope"}

    async def test_a_rejected_strategy_cannot_be_argued_past_the_gate(self) -> None:
        report = await check(recorded(Verdict.REJECTED)).run(["threshold"])

        assert report.outcome is LiveLaunchOutcome.REFUSED
        assert any("not validated" in r or "different configuration" in r
                   for r in report.refusals["threshold"])  # fmt: skip
