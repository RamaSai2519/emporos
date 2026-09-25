"""One recording day, start to finish, for a process that does nothing else (EM-236).

`QuoteRecordingDay` drives a `QuoteRecorder` on the clock: it waits for the session to open, polls
once per interval, and returns when the session has closed and the last rows are written. A
weekend, or a day on which the exchange sent nothing new (every quote stamped an earlier day: an
exchange holiday), ends it cleanly with nothing written. Cancelling it (a stop signal) still writes
what is in memory."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from enum import StrEnum

from emporos.core.clock import IST, Clock, Sleeper
from emporos.quotes.recorder import QuoteRecorder
from emporos.quotes.window import RecordingWindow

__all__ = ["DayOutcome", "QuoteRecordingDay"]

_LOG = logging.getLogger(__name__)


class DayOutcome(StrEnum):
    RECORDED = "recorded"  # the session ran and closed
    NOT_A_WEEKDAY = "not_a_weekday"
    AFTER_CLOSE = "after_close"  # started once the session was over: nothing to do
    HOLIDAY = "holiday"  # the exchange sent no quote stamped today
    INTERRUPTED = "interrupted"  # stopped before the close


class QuoteRecordingDay:
    def __init__(
        self,
        recorder: QuoteRecorder,
        clock: Clock,
        sleeper: Sleeper,
        window: RecordingWindow,
        interval: timedelta = timedelta(seconds=60),
        holiday_after_polls: int = 3,
    ) -> None:
        if holiday_after_polls < 1:
            raise ValueError("decide a holiday after at least one poll")
        self._recorder, self._clock, self._sleeper = recorder, clock, sleeper
        self._window, self._interval = window, interval
        self._holiday_after = holiday_after_polls

    async def run(self) -> DayOutcome:
        if self._clock.now().astimezone(IST).weekday() >= 5:
            return DayOutcome.NOT_A_WEEKDAY
        try:
            outcome = await self._loop()
        except asyncio.CancelledError:
            self._recorder.close()
            _LOG.info("quote recording stopped early: %s", self._recorder.counters)
            raise
        self._recorder.close()
        _LOG.info("quote recording day %s: %s", outcome.value, self._recorder.counters)
        return outcome

    async def _loop(self) -> DayOutcome:
        started = False
        while True:
            now = self._clock.now()
            if self._window.contains(now):
                started = True
                await self._recorder.poll()
                if self._is_holiday():
                    return DayOutcome.HOLIDAY
            elif started or self._window.is_after_close(now):
                return DayOutcome.RECORDED if started else DayOutcome.AFTER_CLOSE
            await self._sleeper.sleep(self._interval.total_seconds())

    def _is_holiday(self) -> bool:
        c = self._recorder.counters
        return c.polls >= self._holiday_after and c.rows == 0 and c.stale_dropped > 0
