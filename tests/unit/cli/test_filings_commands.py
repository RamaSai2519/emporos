"""EM-239: the filings commands: build the events, report them, and rank the attachment fetches."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.cli import filings_commands
from emporos.cli.experiment_commands import research_app
from emporos.research.filings.attachments import AttachmentText, AttachmentTextStore
from emporos.research.filings.raw_store import FetchLedger, FetchRecord, RawFilingStore

RUNNER = CliRunner()
NOW = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


def announcement(seq: str, when: str, desc: str, attachment: str) -> dict[str, object]:
    return {
        "an_dt": when, "exchdisstime": when, "attchmntFile": attachment, "attchmntText": f"t{seq}",
        "desc": desc, "seq_id": seq, "sm_isin": "INE1", "sm_name": "ABB India", "symbol": "ABB",
    }  # fmt: skip


@pytest.fixture
def world(tmp_path: Path) -> Path:
    rows = [
        announcement("1", "05-Mar-2024 10:00:10", "Outcome of Board Meeting", "https://a/1.pdf"),
        announcement("2", "06-Mar-2024 10:00:10", "Trading Window", "https://a/2.pdf"),
    ]
    body = json.dumps(rows).encode()
    raw, ledger = RawFilingStore(tmp_path / "raw"), FetchLedger(tmp_path / "ledger.jsonl")
    first, last = date(2024, 1, 1), date(2024, 12, 31)
    digest = raw.write("NSE", "ABB", first, last, body)
    ledger.record(
        FetchRecord("NSE", "ABB", first, last, "https://nse/x", NOW, 2, len(body), digest)
    )
    AttachmentTextStore(tmp_path / "text").append(
        AttachmentText("https://a/1.pdf", NOW, "ok", "results are up", 10, "h")
    )
    (tmp_path / "tokens.csv").write_text("Symbol,Token\nABB,13\n", encoding="utf-8")
    return tmp_path


def args(root: Path) -> list[str]:
    return [
        "--tokens", str(root / "tokens.csv"), "--raw", str(root / "raw"),
        "--ledger", str(root / "ledger.jsonl"), "--text", str(root / "text"),
        "--events", str(root / "events"),
    ]  # fmt: skip


def test_build_events_then_report_them(world: Path) -> None:
    built = RUNNER.invoke(research_app, ["build-events", *args(world)])
    report = RUNNER.invoke(
        research_app,
        ["filings-report", "--events", str(world / "events"), "--text", str(world / "text")],
    )

    assert built.exit_code == 0, built.output
    assert "2 events in 1 month file(s)" in built.output
    assert report.exit_code == 0, report.output
    assert "2 events, 1 names" in report.output
    assert "2024-03" in report.output
    assert "Outcome of Board Meeting" in report.output


def test_the_built_events_read_back_with_the_pdf_text_and_the_token(world: Path) -> None:
    from emporos.research.filings.event_store import ParquetEventStore

    RUNNER.invoke(research_app, ["build-events", *args(world)])

    (first, second) = ParquetEventStore(world / "events").events_between(
        datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC)
    )

    assert (first.text, first.instrument_id) == ("results are up", "NSE:13")
    assert second.text == "t2"  # its attachment was never fetched: the feed's own line


def test_extract_ranks_the_material_attachments_and_skips_routine_ones(
    world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[list[str]] = []

    async def fake(urls, text, limit, gap, sleeper):  # type: ignore[no-untyped-def]
        asked.append(urls)
        return 0, {}

    monkeypatch.setattr(filings_commands, "_extract", fake)

    result = RUNNER.invoke(
        research_app,
        [
            "extract-filing-text", "--raw", str(world / "raw"),
            "--ledger", str(world / "ledger.jsonl"), "--text", str(world / "text"),
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert asked == [["https://a/1.pdf"]]  # the trading-window notice is routine
    assert "1 attachments in scope, 1 already held" in result.output


def test_a_bad_ledger_is_a_clean_failure(world: Path) -> None:
    (world / "raw" / "nse" / "ABB" / "2024-01-01_2024-12-31.json").unlink()

    result = RUNNER.invoke(research_app, ["build-events", *args(world)])

    assert result.exit_code == 1
    assert "build-events failed" in result.output


def test_freeze_events_writes_a_sealed_snapshot_and_refuses_to_rebuild_it(world: Path) -> None:
    from emporos.research.filings.event_store import ParquetEventStore
    from emporos.research.filings.snapshot import EventSnapshot

    snapshots = world / "snapshots"
    freeze = ["freeze-events", "dev-1", *args(world)[:-2], "--snapshots", str(snapshots)]

    first = RUNNER.invoke(research_app, freeze)
    again = RUNNER.invoke(research_app, freeze)

    root = snapshots / "dev-1"
    try:
        assert first.exit_code == 0, first.output
        assert "2 events" in first.output and "files verified: True" in first.output
        assert again.exit_code == 1 and "already exists" in again.output
        events = ParquetEventStore(root).events_between(
            datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC)
        )
        assert [e.text for e in events] == ["results are up", "t2"]
        assert not (root / "v1" / "manifest.json").stat().st_mode & 0o200  # read-only
    finally:
        EventSnapshot(root).seal_removal()
