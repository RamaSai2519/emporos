"""EM-239: filings become events in a versioned Parquet store, read back by time, name and id."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from emporos.core.clock import IST
from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.event_store import (
    EventBuilder,
    ParquetEventStore,
    dedupe_across_exchanges,
)
from emporos.research.filings.filing import Filing

BUILT = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


def filing(
    source_id: str = "1", symbol: str = "ABB", at: str = "2024-03-05T10:00:10",
    category: str = "Updates", subject: str = "feed text", attachment: str = "https://a/1.pdf",
    source: str = "NSE", isin: str = "INE1",
) -> Filing:  # fmt: skip
    published = datetime.fromisoformat(at).replace(tzinfo=IST)
    return Filing(
        source, symbol, isin, "ABB India", source_id, published, published - timedelta(seconds=6),
        category, subject, attachment, "https://nse/x", date(2026, 9, 25),
    )  # fmt: skip


def text(url: str, status: str = "ok", body: str = "the pdf says results are up") -> AttachmentText:
    return AttachmentText(url, BUILT, status, body, 1000, "h")


class TestBuilder:
    def build(self, filings: list[Filing], texts: dict[str, AttachmentText]):  # type: ignore[no-untyped-def]
        return EventBuilder({"ABB": "NSE:13"}).build(filings, texts)

    def test_an_event_carries_the_pdf_text_the_token_and_the_dissemination_time_in_utc(
        self,
    ) -> None:
        (row,) = self.build([filing()], {"https://a/1.pdf": text("https://a/1.pdf")})

        assert row.event_id == "NSE:1"
        assert (row.instrument_id, row.text, row.text_status) == (
            "NSE:13",
            "the pdf says results are up",
            "ok",
        )
        assert row.published_at == datetime(2024, 3, 5, 4, 30, 10, tzinfo=UTC)
        event = row.as_event()
        assert event.usable_from == event.published_at
        assert event.kind == "filing"

    def test_without_pdf_text_the_feed_subject_stands_in_and_the_status_says_so(self) -> None:
        (never,) = self.build([filing()], {})
        (scan,) = self.build(
            [filing()], {"https://a/1.pdf": text("https://a/1.pdf", "image_only", "")}
        )

        assert (never.text, never.text_status) == ("feed text", "none")
        assert (scan.text, scan.text_status) == ("feed text", "image_only")

    def test_a_name_with_no_token_gets_an_empty_instrument_id(self) -> None:
        (row,) = EventBuilder({}).build([filing()], {})

        assert row.instrument_id == ""

    def test_the_same_filing_seen_twice_is_one_event_and_events_are_ordered(self) -> None:
        rows = self.build([filing("2", at="2024-03-05T11:00:00"), filing("1"), filing("1")], {})

        assert [r.event_id for r in rows] == ["NSE:1", "NSE:2"]


class TestStore:
    def store(self, tmp_path: Path, filings: list[Filing]) -> ParquetEventStore:
        store = ParquetEventStore(tmp_path)
        store.write(EventBuilder({"ABB": "NSE:13"}).build(filings, {}), BUILT, {"note": "x"})
        return store

    FILINGS = (
        filing("1", "ABB", "2024-01-31T23:59:59"),
        filing("2", "TCS", "2024-02-01T00:00:00"),
        filing("3", "ABB", "2024-02-01T09:15:00"),
    )

    def test_events_between_is_half_open_sorted_and_reads_across_months(
        self, tmp_path: Path
    ) -> None:
        store = self.store(tmp_path, list(self.FILINGS))
        start = datetime(2024, 1, 31, 23, 59, 59, tzinfo=IST)

        got = store.events_between(start, datetime(2024, 2, 1, 9, 15, tzinfo=IST))

        assert [e.event_id for e in got] == ["NSE:1", "NSE:2"]  # 09:15:00 itself is outside

    def test_for_symbol_filters_by_trading_symbol(self, tmp_path: Path) -> None:
        store = self.store(tmp_path, list(self.FILINGS))

        got = store.for_symbol(
            "ABB", datetime(2024, 1, 1, tzinfo=IST), datetime(2024, 3, 1, tzinfo=IST)
        )

        assert [e.event_id for e in got] == ["NSE:1", "NSE:3"]

    def test_by_id_finds_the_event_or_none(self, tmp_path: Path) -> None:
        store = self.store(tmp_path, list(self.FILINGS))

        found = store.by_id("NSE:3")

        assert found is not None and found.symbol == "ABB" and found.instrument_id == "NSE:13"
        assert store.by_id("NSE:999") is None

    def test_a_query_outside_the_stored_months_is_empty(self, tmp_path: Path) -> None:
        store = self.store(tmp_path, list(self.FILINGS))

        assert (
            store.events_between(datetime(2030, 1, 1, tzinfo=IST), datetime(2030, 2, 1, tzinfo=IST))
            == []
        )

    def test_a_rebuild_replaces_the_store(self, tmp_path: Path) -> None:
        store = self.store(tmp_path, list(self.FILINGS))
        store.write(EventBuilder({}).build([filing("9")], {}), BUILT, {})

        got = store.events_between(
            datetime(2024, 1, 1, tzinfo=IST), datetime(2024, 12, 31, tzinfo=IST)
        )

        assert [e.event_id for e in got] == ["NSE:9"]

    def test_the_manifest_is_versioned_and_an_unknown_version_is_refused(
        self, tmp_path: Path
    ) -> None:
        store = self.store(tmp_path, list(self.FILINGS))
        manifest = store.directory / "manifest.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))

        assert (document["schema_version"], document["events"], document["note"]) == (1, 3, "x")
        assert document["months"] == ["2024-01", "2024-02"]
        document["schema_version"] = 2
        manifest.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError, match="schema"):
            ParquetEventStore(tmp_path)


class TestDedupe:
    def test_the_same_filing_on_two_exchanges_keeps_the_earlier(self) -> None:
        nse = EventBuilder({}).build([filing("1", at="2024-03-05T10:00:00")], {})
        bse = EventBuilder({}).build(
            [filing("B1", at="2024-03-05T10:04:00", source="BSE", subject="FEED  text")], {}
        )

        (kept,) = dedupe_across_exchanges([*bse, *nse])

        assert kept.source == "NSE"

    def test_a_gap_over_ten_minutes_or_a_different_subject_keeps_both(self) -> None:
        nse = EventBuilder({}).build([filing("1", at="2024-03-05T10:00:00")], {})
        late = EventBuilder({}).build([filing("B1", at="2024-03-05T10:11:00", source="BSE")], {})
        other = EventBuilder({}).build(
            [filing("B2", at="2024-03-05T10:02:00", source="BSE", subject="different")], {}
        )

        assert len(dedupe_across_exchanges([*nse, *late, *other])) == 3

    def test_two_filings_from_one_exchange_are_never_merged(self) -> None:
        rows = EventBuilder({}).build([filing("1"), filing("2")], {})

        assert len(dedupe_across_exchanges(rows)) == 2
