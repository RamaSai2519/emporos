"""A frozen, versioned copy of the event store: what a Dev run reads (PROFIT_PLAN §12, EM-239).

All six variants of a run must see identical event text, and the text keeps arriving while the PDFs
are extracted, so a run reads a SNAPSHOT: the event store written once into its own directory, its
files made read-only, and a `SNAPSHOT.json` recording the sha256 of every file, the counts by text
status and the attachments held at that moment. `EventSnapshot.freeze` refuses an existing snapshot
(a snapshot is never rebuilt in place: a new name is a new snapshot) and `verify` says whether any
file changed since. A reader opens the snapshot's root with `ParquetEventStore` like any other."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from emporos.research.filings.event_store import EventRow, ParquetEventStore

__all__ = ["DEFAULT_SNAPSHOT_DIR", "EventSnapshot"]

DEFAULT_SNAPSHOT_DIR = Path.home() / ".cache" / "emporos" / "event-snapshots"
RECORD = "SNAPSHOT.json"


class EventSnapshot:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def freeze(
        self, rows: Sequence[EventRow], built_at: datetime, notes: Mapping[str, object]
    ) -> dict[str, object]:
        """Write the store into a NEW directory, seal it and record it; the record."""
        if self._root.exists():
            raise ValueError(
                f"snapshot {self._root.name!r} already exists; a snapshot is never rebuilt"
            )
        store = ParquetEventStore(self._root)
        store.write(rows, built_at, notes)
        record: dict[str, object] = {
            "name": self._root.name, "frozen_at": built_at.isoformat(), "events": len(rows),
            "text_status": dict(Counter(r.text_status for r in rows)), **notes,
            "files": self._hashes(store.directory),
        }  # fmt: skip
        (self._root / RECORD).write_text(json.dumps(record, indent=2, sort_keys=True), "utf-8")
        for path in self._root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP)
        return record

    def verify(self) -> bool:
        """True if every file recorded at freeze time is unchanged (and none is missing)."""
        record = json.loads((self._root / RECORD).read_text(encoding="utf-8"))
        store = ParquetEventStore(self._root)
        return dict(record["files"]) == self._hashes(store.directory)

    @staticmethod
    def _hashes(directory: Path) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir())
            if path.is_file()
        }

    def seal_removal(self) -> None:
        """Make a snapshot deletable again (a test's tmp dir, or the operator's clean-up)."""
        for path in [self._root, *self._root.rglob("*")]:
            os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
