"""The lifecycle only moves where plan.md §13 allows; jobs run on schedule and fail in isolation."""

from datetime import timedelta
from itertools import product

import pytest

from emporos.core.clock import FixedClock
from emporos.session.jobs import Job, JobScheduler
from emporos.session.lifecycle import (
    IllegalTransitionError,
    SessionLifecycle,
    SessionState,
    StateChange,
)
from tests.support.fakes import RecordingAlertSink
from tests.support.records import NOW

S = SessionState
HAPPY_PATH = [
    S.AUTHENTICATING, S.RECOVERING, S.CONNECTING, S.READY, S.TRADING, S.SQUARING_OFF,
    S.RECONCILING, S.REPORTING, S.SHUTTING_DOWN,
]  # fmt: skip


def at(state: SessionState) -> SessionLifecycle:
    lifecycle = SessionLifecycle(FixedClock(NOW))
    routes = {
        S.STARTING: [], S.AUTHENTICATING: HAPPY_PATH[:1], S.RECOVERING: HAPPY_PATH[:2],
        S.CONNECTING: HAPPY_PATH[:3], S.READY: HAPPY_PATH[:4], S.TRADING: HAPPY_PATH[:5],
        S.SQUARING_OFF: HAPPY_PATH[:6], S.RECONCILING: HAPPY_PATH[:7],
        S.REPORTING: HAPPY_PATH[:8], S.SHUTTING_DOWN: HAPPY_PATH[:9],
        S.HALTED: [*HAPPY_PATH[:5], S.HALTED], S.FAILED: [S.FAILED],
    }  # fmt: skip
    for step in routes[state]:
        lifecycle.transition(step)
    assert lifecycle.state is state
    return lifecycle


class TestLifecycle:
    def test_the_whole_session_walks_the_happy_path(self) -> None:
        seen: list[StateChange] = []
        lifecycle = SessionLifecycle(FixedClock(NOW), (seen.append,))
        for step in HAPPY_PATH:
            lifecycle.transition(step, "because")
        assert [c.current for c in seen] == HAPPY_PATH and seen[0].previous is S.STARTING
        assert all(c.reason == "because" and c.at == NOW for c in seen)

    def test_trading_is_only_allowed_in_the_trading_state(self) -> None:
        assert [s for s in S if at(s).trading_allowed] == [S.TRADING]

    def test_a_halt_can_be_resumed_and_a_halted_session_still_reconciles(self) -> None:
        lifecycle = at(S.HALTED)
        lifecycle.transition(S.TRADING)
        lifecycle.transition(S.HALTED)
        lifecycle.transition(S.RECONCILING)

    def test_a_dirty_recovery_halts_instead_of_trading(self) -> None:
        lifecycle = at(S.RECOVERING)
        lifecycle.transition(S.HALTED)
        with pytest.raises(IllegalTransitionError):
            lifecycle.transition(S.CONNECTING)

    @pytest.mark.parametrize("old", [s for s in S if s not in (S.FAILED,)])
    def test_any_state_can_fail_and_a_failed_session_goes_nowhere(self, old: S) -> None:
        lifecycle = at(old)
        lifecycle.transition(S.FAILED, "boom")
        for target in S:
            with pytest.raises(IllegalTransitionError):
                lifecycle.transition(target)

    def test_nothing_skips_recovery(self) -> None:
        for old, new in product(S, S):
            if new is S.TRADING and old not in (S.READY, S.HALTED):
                with pytest.raises(IllegalTransitionError):
                    at(old).transition(new)

    def test_an_illegal_move_is_refused_and_changes_nothing(self) -> None:
        lifecycle = at(S.TRADING)
        with pytest.raises(IllegalTransitionError, match="TRADING cannot become READY"):
            lifecycle.transition(S.READY)
        assert lifecycle.state is S.TRADING


class TestJobs:
    def scheduler(self, jobs: list[Job]) -> tuple[JobScheduler, FixedClock, RecordingAlertSink]:
        clock, alerts = FixedClock(NOW), RecordingAlertSink()
        return JobScheduler(jobs, clock, alerts), clock, alerts

    async def test_a_job_runs_at_first_then_only_when_its_interval_has_elapsed(self) -> None:
        calls: list[str] = []

        async def tick() -> None:
            calls.append("tick")

        scheduler, clock, _ = self.scheduler([Job("tick", timedelta(seconds=5), tick)])
        assert await scheduler.run_due() == ["tick"]
        clock.advance(timedelta(seconds=4))
        assert await scheduler.run_due() == []
        clock.advance(timedelta(seconds=1))
        assert await scheduler.run_due() == ["tick"] and calls == ["tick", "tick"]

    async def test_jobs_run_in_declared_order_and_one_failing_does_not_stop_the_rest(self) -> None:
        order: list[str] = []

        async def bad() -> None:
            order.append("bad")
            raise RuntimeError("down")

        async def good() -> None:
            order.append("good")

        scheduler, clock, alerts = self.scheduler(
            [Job("bad", timedelta(seconds=1), bad), Job("good", timedelta(seconds=1), good)]
        )
        assert await scheduler.run_due() == ["bad", "good"]
        assert order == ["bad", "good"] and len(alerts.alerts) == 1
        for _ in range(3):  # still failing: no second alert for the same outage
            clock.advance(timedelta(seconds=1))
            await scheduler.run_due()
        assert [name for name, _ in alerts.alerts] == ["job_failed:bad"]

    async def test_a_recovered_job_alerts_again_if_it_fails_again(self) -> None:
        state = {"fail": True}

        async def flaky() -> None:
            if state["fail"]:
                raise RuntimeError("x")

        scheduler, clock, alerts = self.scheduler([Job("flaky", timedelta(seconds=1), flaky)])
        await scheduler.run_due()
        state["fail"] = False
        clock.advance(timedelta(seconds=1))
        await scheduler.run_due()
        state["fail"] = True
        clock.advance(timedelta(seconds=1))
        await scheduler.run_due()
        assert len(alerts.alerts) == 2

    async def test_run_now_ignores_the_interval(self) -> None:
        calls: list[int] = []

        async def act() -> None:
            calls.append(1)

        scheduler, _, _ = self.scheduler([Job("j", timedelta(hours=1), act)])
        await scheduler.run_due()
        await scheduler.run_now("j")
        assert calls == [1, 1]

    def test_jobs_need_unique_names_and_positive_intervals(self) -> None:
        async def act() -> None: ...

        with pytest.raises(ValueError):
            Job("j", timedelta(0), act)
        with pytest.raises(ValueError, match="unique"):
            self.scheduler([Job("j", timedelta(1), act), Job("j", timedelta(1), act)])
