"""EM-45: spurious rate-limit noise is absorbed; ambiguity on mutating calls never is."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.retry import (
    ANGELONE_RETRY_POLICIES,
    DEFAULT_POLICY,
    DEFECT_POLICY,
    RetryingTransport,
)
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import HttpRestTransport, RestRequest
from emporos.broker.errors import (
    BrokerConnectionError,
    BrokerError,
    BrokerRateLimitedError,
    BrokerRejectedError,
    BrokerRetriesExhaustedError,
    BrokerSessionExpiredError,
    BrokerTransportError,
)
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.core.clock import FixedClock
from emporos.core.errors import ErrorClassification
from tests.support.fakes import (
    AdvancingSleeper,
    FixedJitter,
    ScriptedHttpServer,
    failed_reply,
    ok_reply,
)

RATE_LIMIT_TEXT = "Access denied because of exceeding access rate"
CANDLES = RestRequest(Endpoints.CANDLES, body={"x": 1}, bearer="t")
LOGIN = RestRequest(Endpoints.LOGIN, body={"x": 1})


class ScriptedTransport:
    """`RestTransport` double that raises/returns from a script and counts calls."""

    def __init__(self, *script: BaseException | object) -> None:
        self._script = list(script)
        self.calls = 0

    async def send(self, request: RestRequest) -> Any:
        self.calls += 1
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def spurious() -> BrokerRateLimitedError:
    return BrokerRateLimitedError(RATE_LIMIT_TEXT)  # class attr: RETRYABLE


def retrying(inner: ScriptedTransport, jitter: float = 0.0):  # type: ignore[no-untyped-def]
    clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=UTC))
    sleeper = AdvancingSleeper(clock)
    return RetryingTransport(inner, sleeper, FixedJitter(jitter)), sleeper


async def test_a_burst_of_spurious_rate_limits_is_absorbed_without_the_caller_noticing() -> None:
    inner = ScriptedTransport(spurious(), spurious(), spurious(), {"candles": "ok"})
    transport, sleeper = retrying(inner)

    result = await transport.send(CANDLES)

    assert result == {"candles": "ok"}
    assert inner.calls == 4
    # exponential: each wait's floor doubles (jitter pinned to 0 -> ceiling / 2)
    assert sleeper.sleeps == [0.5, 1.0, 2.0]


async def test_a_persistent_defect_eventually_surfaces_as_a_real_failure() -> None:
    inner = ScriptedTransport(*[spurious() for _ in range(20)])
    transport, sleeper = retrying(inner)

    with pytest.raises(BrokerRetriesExhaustedError) as raised:
        await transport.send(CANDLES)

    assert inner.calls == DEFECT_POLICY.max_attempts == raised.value.attempts
    assert len(sleeper.sleeps) == DEFECT_POLICY.max_attempts - 1
    assert isinstance(raised.value.last_error, BrokerRateLimitedError)
    assert raised.value.__cause__ is raised.value.last_error
    assert raised.value.classification is ErrorClassification.RETRYABLE


async def test_the_defective_endpoints_get_a_more_patient_policy_than_the_rest() -> None:
    assert ANGELONE_RETRY_POLICIES["candles"] is DEFECT_POLICY
    assert ANGELONE_RETRY_POLICIES["order_book"] is DEFECT_POLICY
    assert DEFECT_POLICY.max_attempts > DEFAULT_POLICY.max_attempts

    inner = ScriptedTransport(*[spurious() for _ in range(20)])
    transport, _ = retrying(inner)
    with pytest.raises(BrokerRetriesExhaustedError):
        await transport.send(RestRequest(Endpoints.PROFILE, bearer="t"))
    assert inner.calls == DEFAULT_POLICY.max_attempts


async def test_a_definitive_error_is_never_retried() -> None:
    inner = ScriptedTransport(BrokerRejectedError("no"))
    transport, sleeper = retrying(inner)

    with pytest.raises(BrokerRejectedError):
        await transport.send(CANDLES)

    assert inner.calls == 1 and sleeper.sleeps == []


async def test_an_expired_session_is_left_to_the_session_layer() -> None:
    inner = ScriptedTransport(BrokerSessionExpiredError("Invalid Token"))
    transport, sleeper = retrying(inner)

    with pytest.raises(BrokerSessionExpiredError):
        await transport.send(CANDLES)

    assert inner.calls == 1 and sleeper.sleeps == []


@pytest.mark.parametrize(
    "ambiguous",
    [
        BrokerTransportError("timeout"),
        BrokerRateLimitedError("x", classification=ErrorClassification.AMBIGUOUS),
    ],
)
async def test_an_ambiguous_outcome_on_a_read_is_safe_to_replay(ambiguous: BrokerError) -> None:
    inner = ScriptedTransport(ambiguous, {"ok": 1})
    transport, _ = retrying(inner)

    assert await transport.send(CANDLES) == {"ok": 1}
    assert inner.calls == 2


@pytest.mark.parametrize(
    "ambiguous",
    [
        BrokerTransportError("timeout"),
        BrokerRateLimitedError("x", classification=ErrorClassification.AMBIGUOUS),
    ],
)
async def test_an_ambiguous_outcome_on_a_mutating_call_is_never_replayed(
    ambiguous: BrokerError,
) -> None:
    inner = ScriptedTransport(ambiguous, {"would-be-duplicate": 1})
    transport, sleeper = retrying(inner)

    with pytest.raises(BrokerError) as raised:
        await transport.send(LOGIN)

    assert raised.value is ambiguous
    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    assert inner.calls == 1 and sleeper.sleeps == []


async def test_a_never_sent_request_is_safe_to_retry_even_when_it_mutates() -> None:
    inner = ScriptedTransport(BrokerConnectionError("refused"), {"ok": 1})
    transport, _ = retrying(inner)

    assert await transport.send(LOGIN) == {"ok": 1}
    assert inner.calls == 2


async def test_unexpected_exceptions_are_not_swallowed() -> None:
    inner = ScriptedTransport(RuntimeError("bug"))
    transport, _ = retrying(inner)

    with pytest.raises(RuntimeError):
        await transport.send(CANDLES)
    assert inner.calls == 1


async def test_the_full_stack_absorbs_a_burst_of_spurious_denials_end_to_end() -> None:
    """Real classification + rate limiter + backoff over a scripted HTTP server."""
    clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=UTC))
    sleeper = AdvancingSleeper(clock)
    server = ScriptedHttpServer().queue(
        failed_reply(RATE_LIMIT_TEXT),
        httpx.Response(200, content=b'{"message":"' + RATE_LIMIT_TEXT.encode() + b'"}'),
        failed_reply(RATE_LIMIT_TEXT),
        ok_reply([["2026-09-18T09:15:00+05:30", 1, 2, 0.5, 1, 10]]),
    )
    stack = RetryingTransport(
        RateLimitedTransport(
            HttpRestTransport(server.client(), "key"),
            GroupRateLimiter(ANGELONE_RATE_LIMITS, clock, sleeper),
        ),
        sleeper,
        FixedJitter(0.5),
    )

    data = await stack.send(CANDLES)

    assert data == [["2026-09-18T09:15:00+05:30", 1, 2, 0.5, 1, 10]]
    assert len(server.requests) == 4
    backoffs = [s for s in sleeper.sleeps if s >= 0.75]  # jitter 0.5 -> 0.75x ceiling, doubling
    assert backoffs[:3] == [0.75, 1.5, 3.0]
