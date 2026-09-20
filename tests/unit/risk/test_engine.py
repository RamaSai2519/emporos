"""The engine: ordered evaluation, fail-closed behaviour, and audit-before-answer (EM-73)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.marketdata.session import SessionWindow
from emporos.risk.approval import (
    ApprovalSeal,
    RiskApprovedSignal,
    RiskRejection,
)
from emporos.risk.engine import RULE_ERROR_REASON, SNAPSHOT_RULE, RiskAuditError, RiskEngine
from emporos.risk.standard import StandardRuleSet
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import (
    NOW,
    MemoryRejectionLog,
    ScriptedRule,
    StaticSnapshots,
    generous_limits,
    healthy,
)
from tests.support.strategies import make_signal

REPO = Path(__file__).resolve().parents[3]


class Rig:
    def __init__(self, *rules: ScriptedRule, snapshots: StaticSnapshots | None = None,
                 log: MemoryRejectionLog | None = None) -> None:  # fmt: skip
        self.calls: list[str] = []
        self.snapshots = snapshots or StaticSnapshots(healthy())
        self.log = log or MemoryRejectionLog()
        self.alerts = RecordingAlertSink()
        self.engine = RiskEngine(
            list(rules), self.snapshots, self.log, IdGenerator(), FixedClock(NOW), self.alerts
        )


def rules(log: list[str], *specs: tuple[str, bool]) -> list[ScriptedRule]:
    return [ScriptedRule(name, allow, log=log) for name, allow in specs]


async def test_a_signal_every_rule_allows_is_approved_with_the_rules_it_passed() -> None:
    log: list[str] = []
    rig = Rig(*rules(log, ("A", True), ("B", True), ("C", True)))

    decision = await rig.engine.review(make_signal(), "sig-1")

    assert isinstance(decision, RiskApprovedSignal)
    assert decision.rules_passed == ("A", "B", "C")
    assert decision.signal_id == "sig-1" and decision.approved_at == NOW
    assert decision.approval_id and rig.log.rejections == []


async def test_rules_run_in_the_order_given_and_stop_at_the_first_block() -> None:
    log: list[str] = []
    rig = Rig(*rules(log, ("first", True), ("second", False), ("third", False), ("fourth", True)))

    decision = await rig.engine.review(make_signal(), "sig-1")

    assert log == ["first", "second"]
    assert isinstance(decision, RiskRejection) and decision.rule == "second"
    assert [(t.rule, t.allowed) for t in decision.trace] == [("first", True), ("second", False)]


async def test_reordering_the_rules_changes_which_reason_a_rejection_carries() -> None:
    both_block = [("x", False), ("y", False)]
    forward = await Rig(*rules([], *both_block)).engine.review(make_signal(), "s")
    backward = await Rig(*rules([], *reversed(both_block))).engine.review(make_signal(), "s")
    assert isinstance(forward, RiskRejection) and isinstance(backward, RiskRejection)
    assert (forward.rule, backward.rule) == ("x", "y")


async def test_a_rejection_is_persisted_before_it_is_returned_with_its_full_context() -> None:
    rig = Rig(*rules([], ("ok", True), ("nope", False)))
    signal = make_signal(quantity=42)

    decision = await rig.engine.review(signal, "sig-9")

    assert rig.log.rejections == [decision]
    assert isinstance(decision, RiskRejection)
    assert decision.signal is signal and decision.signal_id == "sig-9"
    assert decision.reason == "nope says no" and decision.rejected_at == NOW
    assert decision.snapshot["now"] == NOW.isoformat()  # the state the rules judged is kept


async def test_a_rule_that_raises_blocks_the_signal_and_raises_an_alert() -> None:
    log: list[str] = []
    boom = ScriptedRule("boom", error=RuntimeError("bug"), log=log)
    rig = Rig(ScriptedRule("ok", log=log), boom, ScriptedRule("after", log=log))

    decision = await rig.engine.review(make_signal(), "s")

    assert isinstance(decision, RiskRejection)
    assert (decision.rule, decision.reason) == ("boom", RULE_ERROR_REASON)
    assert log == ["ok", "boom"]  # nothing runs after a failed rule
    assert rig.alerts.alerts[0][0] == "risk_rule_error"
    assert rig.log.rejections == [decision]


async def test_a_snapshot_that_cannot_be_built_is_a_rejection_never_an_approval() -> None:
    rig = Rig(*rules([], ("ok", True)), snapshots=StaticSnapshots(error=ConnectionError("db down")))

    decision = await rig.engine.review(make_signal(), "s")

    assert isinstance(decision, RiskRejection) and decision.rule == SNAPSHOT_RULE
    assert decision.details["error"] == "ConnectionError" and decision.trace == ()
    assert rig.log.rejections == [decision]


async def test_a_rejection_that_cannot_be_persisted_raises_and_is_still_not_an_approval() -> None:
    rig = Rig(*rules([], ("nope", False)), log=MemoryRejectionLog(error=OSError("mongo down")))

    with pytest.raises(RiskAuditError, match="nope") as caught:
        await rig.engine.review(make_signal(), "s")

    assert isinstance(caught.value.__cause__, OSError)


def test_an_engine_with_no_rules_is_refused_because_it_would_approve_everything() -> None:
    with pytest.raises(ValueError, match="no rules"):
        RiskEngine([], StaticSnapshots(healthy()), MemoryRejectionLog(), IdGenerator(),
                   FixedClock(NOW), RecordingAlertSink())  # fmt: skip


def test_two_rules_with_one_name_are_refused() -> None:
    with pytest.raises(ValueError, match="share a name"):
        Rig(ScriptedRule("dup"), ScriptedRule("dup"))


async def test_every_approval_gets_its_own_id() -> None:
    rig = Rig(ScriptedRule("ok"))
    first = await rig.engine.review(make_signal(), "a")
    second = await rig.engine.review(make_signal(), "a")
    assert isinstance(first, RiskApprovedSignal) and isinstance(second, RiskApprovedSignal)
    assert first.approval_id != second.approval_id


class TestApprovalCannotBeForged:
    def test_an_approval_cannot_be_built_without_the_engines_seal(self) -> None:
        with pytest.raises(TypeError, match="only by the risk engine"):
            RiskApprovedSignal(
                signal=make_signal(), signal_id="s", approval_id="a", approved_at=NOW,
                rules_passed=("x",), seal=object(),  # type: ignore[arg-type]
            )  # fmt: skip

    def test_a_seal_cannot_be_made_outside_the_engine(self) -> None:
        with pytest.raises(TypeError, match="risk engine"):
            ApprovalSeal(object())

    async def test_an_approval_must_name_the_rules_it_passed(self) -> None:
        approved = await Rig(ScriptedRule("ok")).engine.review(make_signal(), "s")
        assert isinstance(approved, RiskApprovedSignal)
        with pytest.raises(ValueError, match="name the rules"):
            RiskApprovedSignal(make_signal(), "s", "a", NOW, (), approved.seal)

    async def test_an_approval_needs_a_utc_time(self) -> None:
        approved = await Rig(ScriptedRule("ok")).engine.review(make_signal(), "s")
        assert isinstance(approved, RiskApprovedSignal)
        with pytest.raises(ValueError, match="UTC"):
            RiskApprovedSignal(
                make_signal(), "s", "a", NOW.replace(tzinfo=None), ("x",), approved.seal
            )


class TestStandardRuleSet:
    def names(self) -> list[str]:
        rules = StandardRuleSet(generous_limits(), SessionWindow()).rules()
        return [rule.name for rule in rules]

    def test_the_rules_run_in_the_order_of_the_plans_table(self) -> None:
        plan = (REPO / "plan.md").read_text(encoding="utf-8")
        section = plan.split("## 11. Risk engine", 1)[1].split("**Kill switch.**", 1)[0]
        in_plan = re.findall(r"^\| `(\w+Guard)` \|", section, flags=re.MULTILINE)
        assert len(in_plan) == 16
        assert self.names() == in_plan

    async def test_the_standard_set_approves_a_sound_signal_and_names_all_sixteen_rules(
        self,
    ) -> None:
        engine = RiskEngine(
            StandardRuleSet(generous_limits(), SessionWindow()).rules(),
            StaticSnapshots(healthy()), MemoryRejectionLog(), IdGenerator(), FixedClock(NOW),
            RecordingAlertSink(),
        )  # fmt: skip
        decision = await engine.review(make_signal(), "s")
        assert isinstance(decision, RiskApprovedSignal)
        assert list(decision.rules_passed) == self.names()

    async def test_the_standard_set_rejects_with_the_first_offending_rule_in_plan_order(
        self,
    ) -> None:
        # kill switch unread AND a fat-finger quantity: the earlier rule owns the reason
        from emporos.risk.snapshot import SystemFacts

        snapshot = healthy(system=SystemFacts(live_trading_enabled=True))
        engine = RiskEngine(
            StandardRuleSet(generous_limits(), SessionWindow()).rules(),
            StaticSnapshots(snapshot), MemoryRejectionLog(), IdGenerator(), FixedClock(NOW),
            RecordingAlertSink(),
        )  # fmt: skip
        decision = await engine.review(make_signal(quantity=10_000), "s")
        assert isinstance(decision, RiskRejection) and decision.rule == "KillSwitchGuard"


def test_the_engine_reports_the_rules_it_runs_in_order() -> None:
    rig = Rig(ScriptedRule("a"), ScriptedRule("b"))
    assert rig.engine.rule_names == ("a", "b")
