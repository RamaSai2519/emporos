"""Exponential backoff with jitter (EM-45). Broker-agnostic.

The delay before retry *n* (0-based) is drawn from `[ceiling/2, ceiling]`, where
`ceiling = min(max_delay, base_delay * multiplier**n)` ("equal jitter"). The exponential
ceiling backs a struggling endpoint off geometrically; the random half decorrelates clients so
retries do not arrive in lock-step; and the guaranteed lower half means a run of lucky draws can
never collapse the backoff to near zero (which pure "full jitter" allows).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from emporos.core.errors import ConfigurationError


class JitterSource(Protocol):
    def fraction(self) -> float:
        """A value in [0, 1)."""
        ...


class RandomJitter:
    def __init__(self, seed: int | None = None) -> None:
        self._random = random.Random(seed)  # jitter, not security

    def fraction(self) -> float:
        return self._random.random()


@dataclass(frozen=True)
class BackoffPolicy:
    """`max_attempts` counts every try, including the first, so 1 means "never retry"."""

    base_delay: float
    max_delay: float
    max_attempts: int
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ConfigurationError("backoff needs 0 < base_delay <= max_delay")
        if self.multiplier < 1.0:
            raise ConfigurationError("backoff multiplier must be >= 1")
        if self.max_attempts < 1:
            raise ConfigurationError("backoff needs at least one attempt")

    def ceiling(self, retry_index: int) -> float:
        return min(self.max_delay, self.base_delay * self.multiplier**retry_index)

    def delay(self, retry_index: int, jitter: JitterSource) -> float:
        ceiling = self.ceiling(retry_index)
        return ceiling / 2 + jitter.fraction() * ceiling / 2
