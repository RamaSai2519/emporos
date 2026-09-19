"""Per-endpoint-group rate limiting (plan.md §1.4, EM-44).

Angel One enforces per-second, per-minute and per-hour caps *independently* — the per-minute
cap is not derived from the per-second one. Each cap here is a strict sliding window: a call is
admitted only if it keeps every window at or under its limit, so no interval of that length can
ever exceed it.

Why not a textbook token bucket? A bucket that refills at N/window admits up to ~2N in a window
(drain the full bucket, then it refills as fast as you spend it), which breaches a per-minute cap
the broker really enforces; pacing it to burst-1 avoids that but would cap 5000/hour traffic at
~1.4 req/s. Sliding windows compose exactly: bursts up to the smallest cap, bounded by every
larger one. Excess demand is *throttled* (the caller waits), never rejected.

Nothing here knows about a broker: groups are opaque strings, so any adapter can reuse it.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emporos.core.clock import Clock, Sleeper
from emporos.core.errors import ConfigurationError

# Tolerance for float/µs rounding when deciding a grant has aged out of a window.
_EPSILON = 1e-6


@dataclass(frozen=True)
class Window:
    limit: int
    seconds: float


@dataclass(frozen=True)
class RateLimit:
    """The caps for one endpoint group. Unset windows are not enforced."""

    per_second: int
    per_minute: int | None = None
    per_hour: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("per_second", self.per_second),
            ("per_minute", self.per_minute),
            ("per_hour", self.per_hour),
        ):
            if value is not None and value <= 0:
                raise ConfigurationError(f"{name} must be positive, got {value}")

    def windows(self) -> tuple[Window, ...]:
        caps = ((self.per_second, 1.0), (self.per_minute, 60.0), (self.per_hour, 3600.0))
        return tuple(Window(limit, seconds) for limit, seconds in caps if limit is not None)


class SlidingWindow:
    """Remembers when recent calls were admitted and how long until one more would fit."""

    def __init__(self, window: Window) -> None:
        self._window = window
        self._grants: deque[float] = deque()

    def wait_time(self, now: float) -> float:
        self._expire(now)
        if len(self._grants) < self._window.limit:
            return 0.0
        return max(self._grants[0] + self._window.seconds - now, 0.0)

    def record(self, now: float) -> None:
        self._grants.append(now)

    def _expire(self, now: float) -> None:
        cutoff = now - self._window.seconds + _EPSILON
        while self._grants and self._grants[0] <= cutoff:
            self._grants.popleft()


class RateLimiter(Protocol):
    """What a transport needs: wait until a call in `group` may be sent."""

    async def acquire(self, group: str) -> None: ...


class _Elapsed:
    """Seconds since construction, from the injected clock (small numbers keep µs exact)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._origin: datetime = clock.now()

    def __call__(self) -> float:
        return (self._clock.now() - self._origin).total_seconds()


class _GroupLimiter:
    """All windows of one group, behind a FIFO lock so waiters are served in arrival order."""

    def __init__(self, limit: RateLimit) -> None:
        self._windows = [SlidingWindow(window) for window in limit.windows()]
        self._lock = asyncio.Lock()

    async def acquire(self, now: _Elapsed, sleeper: Sleeper) -> None:
        async with self._lock:
            while True:
                wait = max(window.wait_time(now()) for window in self._windows)
                if wait <= 0.0:
                    stamp = now()
                    for window in self._windows:
                        window.record(stamp)
                    return
                await sleeper.sleep(wait)


class GroupRateLimiter:
    """Enforces a `RateLimit` per endpoint group. An unknown group is a wiring bug and fails
    closed rather than silently going unthrottled."""

    def __init__(self, limits: Mapping[str, RateLimit], clock: Clock, sleeper: Sleeper) -> None:
        self._groups = {group: _GroupLimiter(limit) for group, limit in limits.items()}
        self._elapsed = _Elapsed(clock)
        self._sleeper = sleeper

    async def acquire(self, group: str) -> None:
        limiter = self._groups.get(group)
        if limiter is None:
            raise ConfigurationError(f"no rate limit configured for endpoint group {group!r}")
        await limiter.acquire(self._elapsed, self._sleeper)
