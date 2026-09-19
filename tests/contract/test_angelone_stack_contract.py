"""EM-47 + EM-45: the RECORDED rate-limit denial, replayed through the full transport stack.

`login_rate_limited` is a real capture: a second login inside the 1/s window is answered with
HTTP 403 and the plain-text body "Access denied because of exceeding access rate" — the same
denial plan.md §1.4 reports spuriously for getCandleData. It is neither a 429 nor JSON, which
is exactly why it must be recorded rather than assumed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.retry import DEFECT_POLICY, RetryingTransport
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import HttpRestTransport, RestRequest
from emporos.broker.errors import BrokerRateLimitedError, BrokerRetriesExhaustedError
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.core.clock import FixedClock
from emporos.core.errors import ErrorClassification
from tests.support.angelone_fixtures import FixtureLibrary
from tests.support.fakes import AdvancingSleeper, FixedJitter, ScriptedHttpServer, ok_reply

pytestmark = pytest.mark.contract

DENIAL = FixtureLibrary().get("login_rate_limited")
CANDLE_ROW = [["2026-09-18T09:15:00+05:30", 992.0, 992.0, 989.0, 989.6, 35003]]


def stack(server: ScriptedHttpServer) -> tuple[RetryingTransport, AdvancingSleeper]:
    clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=UTC))
    sleeper = AdvancingSleeper(clock)
    transport = RetryingTransport(
        RateLimitedTransport(
            HttpRestTransport(server.client(), "key"),
            GroupRateLimiter(ANGELONE_RATE_LIMITS, clock, sleeper),
        ),
        sleeper,
        FixedJitter(0.5),
    )
    return transport, sleeper


def test_the_recording_is_the_403_plain_text_shape_observed_live() -> None:
    assert DENIAL.provenance == "recorded"
    assert DENIAL.status == 403 and DENIAL.body is None
    assert DENIAL.body_text == "Access denied because of exceeding access rate"


async def test_the_real_denial_is_absorbed_on_a_read() -> None:
    server = ScriptedHttpServer().queue(DENIAL.response(), DENIAL.response(), ok_reply(CANDLE_ROW))
    transport, sleeper = stack(server)

    data = await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="t"))

    assert [row[0] for row in data] == [CANDLE_ROW[0][0]]  # the good reply, parsed
    assert len(server.requests) == 3
    assert sleeper.sleeps == [0.75, 1.5]  # exponential from a 1s base, jitter pinned at 0.5


async def test_a_persistent_real_denial_surfaces_as_a_bounded_real_failure() -> None:
    server = ScriptedHttpServer().queue(
        *[DENIAL.response() for _ in range(DEFECT_POLICY.max_attempts)]
    )
    transport, _ = stack(server)

    with pytest.raises(BrokerRetriesExhaustedError) as raised:
        await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="t"))

    assert len(server.requests) == DEFECT_POLICY.max_attempts
    assert isinstance(raised.value.last_error, BrokerRateLimitedError)
    assert raised.value.classification is ErrorClassification.RETRYABLE


async def test_the_real_denial_on_a_mutating_call_is_never_replayed() -> None:
    """Decision 7: a rate-limited login/order is ambiguous, surfaced once, left to the caller."""
    server = ScriptedHttpServer().queue(DENIAL.response(), ok_reply({"would-be-duplicate": 1}))
    transport, sleeper = stack(server)

    with pytest.raises(BrokerRateLimitedError) as raised:
        await transport.send(RestRequest(Endpoints.LOGIN, body={}))

    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    assert len(server.requests) == 1 and sleeper.sleeps == []
