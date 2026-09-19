"""EM-44: Angel One's limits table and the rate-limiting transport decorator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from emporos.broker.angelone.endpoints import Endpoint, EndpointGroup, Endpoints
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import RestRequest
from emporos.broker.errors import BrokerRejectedError
from emporos.broker.ratelimit import GroupRateLimiter, RateLimit
from emporos.core.clock import FixedClock
from tests.support.fakes import AdvancingSleeper


class RecordingTransport:
    """`RestTransport` double that logs when it was called and can be told to fail."""

    def __init__(self, clock: FixedClock, error: Exception | None = None) -> None:
        self._clock = clock
        self._error = error
        self.calls: list[tuple[str, float]] = []

    async def send(self, request: RestRequest) -> Any:
        self.calls.append((request.endpoint.name, self._clock.now().timestamp()))
        if self._error:
            raise self._error
        return {"ok": True}


def build(limits: dict[str, RateLimit], error: Exception | None = None):  # type: ignore[no-untyped-def]
    clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=UTC))
    sleeper = AdvancingSleeper(clock)
    inner = RecordingTransport(clock, error)
    transport = RateLimitedTransport(inner, GroupRateLimiter(limits, clock, sleeper))
    return transport, inner, sleeper


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        (EndpointGroup.LOGIN, (1, None, None)),
        (EndpointGroup.PLACE_ORDER, (5, 500, 1000)),  # documented 20/s; designed for <= 5/s
        (EndpointGroup.ORDER_BOOK, (1, None, None)),
        (EndpointGroup.LTP, (10, 500, 5000)),
        (EndpointGroup.POSITION, (1, None, None)),
        (EndpointGroup.SEARCH_SCRIP, (1, None, None)),
        (EndpointGroup.HOLDING, (1, None, None)),
        (EndpointGroup.QUOTE, (10, 500, 5000)),
        (EndpointGroup.CANDLES, (3, 180, 5000)),
    ],
)
def test_limits_match_the_published_table(
    group: EndpointGroup, expected: tuple[int, int | None, int | None]
) -> None:
    limit = ANGELONE_RATE_LIMITS[group.value]
    assert (limit.per_second, limit.per_minute, limit.per_hour) == expected


def test_every_endpoint_group_and_every_catalog_endpoint_has_a_limit() -> None:
    assert set(ANGELONE_RATE_LIMITS) == {group.value for group in EndpointGroup}
    endpoints = [v for v in vars(Endpoints).values() if isinstance(v, Endpoint)]
    assert endpoints and all(e.group.value in ANGELONE_RATE_LIMITS for e in endpoints)


def test_the_limits_table_cannot_be_mutated() -> None:
    with pytest.raises(TypeError):
        ANGELONE_RATE_LIMITS["login"] = RateLimit(per_second=1000)  # type: ignore[index]


async def test_the_decorator_throttles_each_call_by_its_endpoint_group() -> None:
    transport, inner, _ = build({"candles": RateLimit(per_second=1)})

    for _ in range(3):
        await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="t"))

    times = [t for _, t in inner.calls]
    assert times[1] - times[0] >= 1.0 and times[2] - times[1] >= 1.0


async def test_a_failing_call_still_consumed_its_slot_and_the_error_propagates() -> None:
    transport, inner, sleeper = build(
        {"candles": RateLimit(per_second=1)}, BrokerRejectedError("x")
    )

    with pytest.raises(BrokerRejectedError):
        await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="t"))
    with pytest.raises(BrokerRejectedError):
        await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="t"))

    assert len(inner.calls) == 2
    assert sum(sleeper.sleeps) >= 1.0  # the second attempt waited for the window
