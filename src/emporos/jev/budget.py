"""A hard ceiling on paid Jev calls (EM-187).

A backtest can ask the model thousands of questions, and a recording run is real money. The
ceiling is checked BEFORE each call and raises rather than degrading to an ABSTAIN: an experiment
that quietly stopped asking would look like a Jev that rejected everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.core.errors import DefinitiveError
from emporos.jev.models import JevDecision, JevRequest
from emporos.jev.protocol import JevProvider


class JevBudgetExceeded(DefinitiveError):
    """The recording run reached its declared cap on live Jev calls."""


class CallBudgetJevProvider:
    """Counts only calls that reach `inner`: wrap the live client, inside any journal, so a
    question answered from the journal is free."""

    def __init__(self, inner: JevProvider, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError("a Jev call budget must allow at least one call")
        self._inner = inner
        self._max_calls = max_calls
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    async def decide(self, request: JevRequest) -> JevDecision:
        if self._used >= self._max_calls:
            raise JevBudgetExceeded(
                f"the recording run reached its cap of {self._max_calls} live Jev call(s); "
                "raise --max-requests deliberately to record more"
            )
        self._used += 1
        return await self._inner.decide(request)


@dataclass(frozen=True)
class JevSpendCeiling:
    """The most a recording run can spend: an upper bound, since the number of questions a
    backtest asks is not known until it runs, but the cap on calls is."""

    max_calls: int
    tokens_per_call: int  # an ESTIMATE of prompt plus completion; the real count comes back
    inr_per_1k_tokens: Decimal

    @property
    def tokens(self) -> int:
        return self.max_calls * self.tokens_per_call

    @property
    def inr(self) -> Decimal:
        return Decimal(self.tokens) / Decimal(1000) * self.inr_per_1k_tokens
