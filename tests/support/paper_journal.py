"""A `PaperJournal` double that keeps every entry, and can be told to fail its next flush."""

from __future__ import annotations

from emporos.broker.paper.journal import (
    FillRecorded,
    JournalEntry,
    OrderStateRecorded,
    SnapshotRecorded,
)


class RecordingJournal:
    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []
        self.flushes = 0
        self.fail_next_flush = False

    def append(self, entry: JournalEntry) -> None:
        self.entries.append(entry)

    async def flush(self) -> None:
        if self.fail_next_flush:
            self.fail_next_flush = False
            raise OSError("journal store unavailable")
        self.flushes += 1

    def of(
        self, kind: type[OrderStateRecorded | FillRecorded | SnapshotRecorded]
    ) -> list[JournalEntry]:
        return [e for e in self.entries if isinstance(e, kind)]
