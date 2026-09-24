"""Counting what a Jev provider actually did during a run (EM-187).

`JevRunSummary` on a backtest result reports tokens and rejections, but it cannot tell an
experiment whether the model was ever asked successfully. A replay that misses the journal, or a
live call that times out, comes back as a failed decision that the filter quietly treats as a
rejection; an experiment built on those would be measuring the outage, not Jev. The tally makes
the failures countable so the caller can refuse the result.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.jev.models import JevDecision, JevRequest
from emporos.jev.protocol import JevProvider
from emporos.jev.replay import NOT_RECORDED


@dataclass
class JevOutcomeTally:
    requests: int = 0
    failures: int = 0
    not_recorded: int = 0

    @property
    def complete(self) -> bool:
        """True when every question was answered by the model or the journal."""
        return self.failures == 0


class TallyingJevProvider:
    """A `JevProvider` decorator that counts outcomes and otherwise changes nothing."""

    def __init__(self, inner: JevProvider, tally: JevOutcomeTally) -> None:
        self._inner = inner
        self._tally = tally

    async def decide(self, request: JevRequest) -> JevDecision:
        decision = await self._inner.decide(request)
        self._tally.requests += 1
        if not decision.ok:
            self._tally.failures += 1
            if decision.error == NOT_RECORDED:
                self._tally.not_recorded += 1
        return decision
