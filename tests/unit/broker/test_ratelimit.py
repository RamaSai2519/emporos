"""EM-44: per-window caps hold under sustained load; bursts are throttled, not rejected."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from emporos.broker.ratelimit import GroupRateLimiter, RateLimit
from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from tests.support.fakes import AdvancingSleeper

START = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)


class Rig:
    """A limiter over virtual time that records when each call was admitted."""

    def __init__(self, **limits: RateLimit) -> None:
        self.clock = FixedClock(START)
        self.sleeper = AdvancingSleeper(self.clock)
        self.limiter = GroupRateLimiter(limits, self.clock, self.sleeper)
        self.grants: list[float] = []
        self.grant_micros: list[int] = []

    def elapsed(self) -> float:
        return (self.clock.now() - START).total_seconds()

    def _micros(self) -> int:
        """Exact integer µs since start — window arithmetic on floats mis-sorts boundary grants."""
        return (self.clock.now() - START) // timedelta(microseconds=1)

    async def call(self, group: str = "g") -> None:
        await self.limiter.acquire(group)
        self.grants.append(self.elapsed())
        self.grant_micros.append(self._micros())

    async def run(self, calls: int, group: str = "g") -> None:
        await asyncio.gather(*(self.call(group) for _ in range(calls)))

    def max_in_any_window(self, seconds: float) -> int:
        """The most grants that ever fell inside one interval of `seconds` (half-open)."""
        span = round(seconds * 1_000_000)
        return max(
            sum(1 for other in self.grant_micros if start <= other < start + span)
            for start in self.grant_micros
        )


async def test_a_burst_within_every_cap_is_admitted_immediately() -> None:
    rig = Rig(g=RateLimit(per_second=5, per_minute=50))

    await rig.run(5)

    assert rig.grants == [0.0] * 5
    assert rig.sleeper.sleeps == []


async def test_a_burst_over_the_per_second_cap_is_throttled_not_rejected() -> None:
    rig = Rig(g=RateLimit(per_second=3))

    await rig.run(7)  # no exception: the excess simply waits

    assert len(rig.grants) == 7
    assert rig.max_in_any_window(1.0) <= 3
    assert rig.grants[-1] >= 2.0  # 3 + 3 + 1 across three one-second windows


async def test_the_per_minute_cap_binds_even_when_the_per_second_cap_would_allow_more() -> None:
    rig = Rig(g=RateLimit(per_second=10, per_minute=25))

    await rig.run(60)

    assert rig.max_in_any_window(1.0) <= 10
    assert rig.max_in_any_window(60.0) <= 25
    assert rig.grants[25] >= 60.0  # the 26th call must wait out the minute


async def test_the_per_second_cap_binds_even_when_the_per_minute_cap_would_allow_more() -> None:
    rig = Rig(g=RateLimit(per_second=2, per_minute=1000))

    await rig.run(20)

    assert rig.max_in_any_window(1.0) <= 2
    assert rig.grants[-1] >= 9.0


async def test_the_per_hour_cap_binds_independently() -> None:
    rig = Rig(g=RateLimit(per_second=10, per_minute=30, per_hour=50))

    await rig.run(120)

    assert rig.max_in_any_window(1.0) <= 10
    assert rig.max_in_any_window(60.0) <= 30
    assert rig.max_in_any_window(3600.0) <= 50
    assert rig.grants[50] >= 3600.0


async def test_sustained_load_never_exceeds_any_cap_at_any_instant() -> None:
    """The acceptance test: hammer the limiter for ~ten virtual minutes, check every window."""
    rig = Rig(g=RateLimit(per_second=3, per_minute=100, per_hour=400))

    await rig.run(500)

    for seconds, cap in ((1.0, 3), (60.0, 100), (3600.0, 400)):
        assert rig.max_in_any_window(seconds) <= cap, f"{cap} per {seconds}s exceeded"
    assert len(rig.grants) == 500


async def test_the_cap_still_holds_when_calls_arrive_spread_over_time() -> None:
    rig = Rig(g=RateLimit(per_second=4, per_minute=20))

    for _ in range(80):
        await rig.call()
        rig.clock.advance(_ms(120))  # steady trickle, ~8 calls/second offered

    assert rig.max_in_any_window(1.0) <= 4
    assert rig.max_in_any_window(60.0) <= 20


async def test_waiters_are_served_in_arrival_order() -> None:
    rig = Rig(g=RateLimit(per_second=1))
    order: list[int] = []

    async def call(n: int) -> None:
        await rig.limiter.acquire("g")
        order.append(n)

    tasks = []
    for n in range(6):
        tasks.append(asyncio.create_task(call(n)))
        await asyncio.sleep(0)  # arrival order is creation order
    await asyncio.gather(*tasks)

    assert order == list(range(6))


async def test_groups_are_throttled_independently() -> None:
    rig = Rig(slow=RateLimit(per_second=1), fast=RateLimit(per_second=100))

    await rig.run(3, "slow")
    slow_finished_at = rig.elapsed()
    rig.grants.clear()
    rig.grant_micros.clear()
    await rig.run(50, "fast")

    assert slow_finished_at >= 2.0
    assert rig.grants == [slow_finished_at] * 50  # the busy group did not delay the other


async def test_an_unknown_group_fails_closed() -> None:
    rig = Rig(g=RateLimit(per_second=1))
    with pytest.raises(ConfigurationError):
        await rig.limiter.acquire("nope")


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_limits_are_rejected(bad: int) -> None:
    with pytest.raises(ConfigurationError):
        RateLimit(per_second=bad)
    with pytest.raises(ConfigurationError):
        RateLimit(per_second=1, per_minute=bad)
    with pytest.raises(ConfigurationError):
        RateLimit(per_second=1, per_hour=bad)


def test_unset_windows_are_not_enforced() -> None:
    assert [w.seconds for w in RateLimit(per_second=1).windows()] == [1.0]
    assert [w.seconds for w in RateLimit(1, 2, 3).windows()] == [1.0, 60.0, 3600.0]


def _ms(milliseconds: int) -> timedelta:
    return timedelta(milliseconds=milliseconds)
