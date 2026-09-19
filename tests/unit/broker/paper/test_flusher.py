"""The journal flusher: a cadence, alerts on failure, and a last flush at shutdown."""

from __future__ import annotations

import asyncio

import pytest

from emporos.broker.paper.flusher import JournalFlusher
from tests.support.paper_journal import RecordingJournal


class Alerts:
    def __init__(self) -> None:
        self.raised: list[str] = []

    def raise_alert(self, name: str, message: str) -> None:
        self.raised.append(name)


class TickingSleeper:
    """Lets `n` sleeps pass, then blocks forever so the task can be cancelled."""

    def __init__(self, n: int) -> None:
        self._left = n
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if self._left == 0:
            await asyncio.Event().wait()
        self._left -= 1


async def test_it_flushes_every_interval_and_once_more_when_cancelled() -> None:
    journal, sleeper = RecordingJournal(), TickingSleeper(3)
    task = asyncio.create_task(JournalFlusher(journal, sleeper, Alerts(), 2.5).run())
    while len(sleeper.slept) < 4:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sleeper.slept[:3] == [2.5, 2.5, 2.5]
    assert journal.flushes == 3 + 1  # three on the cadence, one at shutdown


async def test_a_failed_flush_alerts_and_the_next_interval_retries() -> None:
    journal, alerts = RecordingJournal(), Alerts()
    flusher = JournalFlusher(journal, TickingSleeper(0), alerts, 1)
    journal.fail_next_flush = True

    await flusher.flush_once()
    await flusher.flush_once()

    assert alerts.raised == ["paper_journal_flush_failed"] and journal.flushes == 1


def test_the_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        JournalFlusher(RecordingJournal(), TickingSleeper(0), Alerts(), 0)
