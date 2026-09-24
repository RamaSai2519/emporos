from __future__ import annotations

from emporos.jev.models import failed_decision
from emporos.jev.replay import NOT_RECORDED
from emporos.jev.tally import JevOutcomeTally, TallyingJevProvider
from tests.support.jev import JEV_T0, RecordingProvider, make_jev_decision, make_jev_request


async def test_successes_are_counted_and_the_decision_is_passed_through() -> None:
    tally = JevOutcomeTally()
    request = make_jev_request()
    decision = make_jev_decision(request)

    result = await TallyingJevProvider(RecordingProvider(decision), tally).decide(request)

    assert result == decision
    assert (tally.requests, tally.failures, tally.not_recorded) == (1, 0, 0)
    assert tally.complete


async def test_failures_and_unrecorded_questions_are_counted_separately() -> None:
    tally = JevOutcomeTally()
    request = make_jev_request()
    timeout = failed_decision("p", error="timeout", requested_at=JEV_T0)
    missing = failed_decision("replay", error=NOT_RECORDED, requested_at=JEV_T0)

    await TallyingJevProvider(RecordingProvider(timeout), tally).decide(request)
    await TallyingJevProvider(RecordingProvider(missing), tally).decide(request)

    assert (tally.requests, tally.failures, tally.not_recorded) == (2, 2, 1)
    assert not tally.complete
