"""EM-239: a frozen event snapshot: written once, sealed, verifiable."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from emporos.research.filings.event_store import EventRow, ParquetEventStore
from emporos.research.filings.snapshot import EventSnapshot

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)


def row(seq: str, status: str) -> EventRow:
    return EventRow(
        f"NSE:{seq}", "NSE", "ABB", "NSE:13", "INE1", "ABB India",
        datetime(2024, 3, 5, 4, 30, tzinfo=UTC), None, "Updates", "s", "t", status, "", "u",
        date(2026, 9, 25),
    )  # fmt: skip


@pytest.fixture
def snapshot(tmp_path: Path) -> Iterator[EventSnapshot]:
    made = EventSnapshot(tmp_path / "dev-1")
    yield made
    if made.root.exists():
        made.seal_removal()


def test_a_snapshot_records_counts_hashes_and_reads_back(snapshot: EventSnapshot) -> None:
    record = snapshot.freeze([row("1", "ok"), row("2", "pending")], NOW, {"attachments_held": 1})

    assert record["events"] == 2 and record["text_status"] == {"ok": 1, "pending": 1}
    assert record["attachments_held"] == 1 and record["name"] == "dev-1"
    assert snapshot.verify()
    events = ParquetEventStore(snapshot.root).events_between(
        datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC)
    )
    assert [e.event_id for e in events] == ["NSE:1", "NSE:2"]


def test_a_snapshot_is_never_rebuilt_in_place(snapshot: EventSnapshot) -> None:
    snapshot.freeze([row("1", "ok")], NOW, {})
    with pytest.raises(ValueError, match="never rebuilt"):
        snapshot.freeze([row("1", "ok"), row("2", "ok")], NOW, {})


def test_a_changed_file_fails_verification(snapshot: EventSnapshot) -> None:
    snapshot.freeze([row("1", "ok")], NOW, {})
    month = next((snapshot.root / "v1").glob("month=*.parquet"))
    month.chmod(0o600)
    month.write_bytes(month.read_bytes() + b"x")
    assert not snapshot.verify()
