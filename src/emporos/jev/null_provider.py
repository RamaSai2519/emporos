"""The default `JevProvider`: Jev disabled or not wired in. Every mode's caller can be written
against `JevProvider` unconditionally — no `if jev_enabled` branch anywhere downstream — because
`NullJevProvider` always abstains, at zero latency and zero cost, which is the correct answer to
"what does Jev say" when Jev was never asked.
"""

from __future__ import annotations

from emporos.core.clock import Clock, SystemClock
from emporos.jev.models import ABSTAIN, JevDecision, JevRequest


class NullJevProvider:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=ABSTAIN,
            confidence=None,
            provider="null",
            model=None,
            config_version=None,
            requested_at=self._clock.now(),
            latency_ms=0,
        )
