"""A daily token cap for the complimentary allowance (EM-240): gpt-4o-mini on the operator's key is
free up to a per-UTC-day token allowance, so the run counts tokens (input plus output) by UTC day
and PAUSES when the next call could pass the cap, resuming after 00:00 UTC. Nothing is dropped and
nothing is asked twice: every answered call is in the journal, and the calls that waited are simply
made once the day turns.

Counting is by the moment a call was made (each recording carries it), so a re-run in a new process
starts from what the journal says was used today. A call is reserved at its worst case (every
character a token, plus the whole output allowance) before it is made, so calls in flight cannot
carry the day past the cap."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta

from emporos.core.clock import Clock, Sleeper
from emporos.eventtrader.llm.client import LlmClient, LlmReply, LlmRequest
from emporos.eventtrader.llm.journal import Recording

__all__ = ["DailyTokenCap", "TokenCappedClient", "usage_by_day"]

MIDNIGHT_MARGIN = timedelta(seconds=60)


def usage_by_day(recordings: Iterable[Recording], model: str) -> dict[date, int]:
    """Tokens the journal holds per UTC day for one model (a recording with no time is skipped)."""
    used: dict[date, int] = defaultdict(int)
    for r in recordings:
        if r.model == model and r.recorded_at:
            used[datetime.fromisoformat(r.recorded_at).astimezone(UTC).date()] += (
                r.tokens_in + r.tokens_out
            )
    return dict(used)


class DailyTokenCap:
    def __init__(
        self,
        cap: int,
        used: Mapping[date, int],
        clock: Clock,
        sleeper: Sleeper,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        if cap < 1:
            raise ValueError("a daily cap is at least one token")
        self._cap, self._clock, self._sleeper, self._log = cap, clock, sleeper, log
        self._used: dict[date, int] = defaultdict(int, used)
        self._reserved = 0
        self.pauses = 0

    @property
    def cap(self) -> int:
        return self._cap

    def usage(self) -> dict[date, int]:
        """Tokens used per UTC day, this run's and the journal's before it."""
        return dict(sorted(self._used.items()))

    async def reserve(self, worst_tokens: int) -> None:
        """Wait, if need be, until the worst case of this call fits in a day's allowance."""
        if worst_tokens > self._cap:
            raise ValueError("one call is larger than a whole day's allowance")
        while True:
            today = self._clock.now().astimezone(UTC).date()
            if self._used[today] + self._reserved + worst_tokens <= self._cap:
                self._reserved += worst_tokens
                return
            self.pauses += 1
            wake = datetime.combine(today + timedelta(days=1), time(0), tzinfo=UTC)
            wake += MIDNIGHT_MARGIN
            wait = (wake - self._clock.now()).total_seconds()
            self._log(
                f"daily token cap reached on {today} ({self._used[today]:,} of {self._cap:,}); "
                f"pausing {wait / 3600:.1f} h until {wake.isoformat()}"
            )
            await self._sleeper.sleep(max(wait, 1.0))

    def release(self, worst_tokens: int) -> None:
        self._reserved -= worst_tokens

    def record(self, tokens: int, worst_tokens: int) -> None:
        self._reserved -= worst_tokens
        self._used[self._clock.now().astimezone(UTC).date()] += tokens


class TokenCappedClient:
    """Sits inside the journal, so a recorded answer is free and never waits."""

    def __init__(self, inner: LlmClient, cap: DailyTokenCap) -> None:
        self._inner, self._cap = inner, cap

    async def complete(self, request: LlmRequest) -> LlmReply:
        worst = len(request.system) + len(request.user) + request.max_output_tokens
        await self._cap.reserve(worst)
        try:
            reply = await self._inner.complete(request)
        except BaseException:
            self._cap.release(worst)
            raise
        self._cap.record(reply.tokens_in + reply.tokens_out, worst)
        return reply
