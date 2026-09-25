"""EM-239: which attachments come first, and the numbers the collection reports."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from emporos.core.clock import IST
from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.event_store import EventBuilder
from emporos.research.filings.filing import Filing
from emporos.research.filings.priority import AttachmentPriority
from emporos.research.filings.report import filing_report_lines


def filing(source_id: str, category: str, at: str, url: str = "") -> Filing:
    published = datetime.fromisoformat(at).replace(tzinfo=IST)
    return Filing(
        "NSE", "ABB", "INE1", "ABB", source_id, published, published - timedelta(seconds=10),
        category, f"s{source_id}", url or f"https://a/{source_id}.pdf", "u", date(2026, 9, 25),
    )  # fmt: skip


FILINGS = [
    filing("1", "Updates", "2024-01-05T10:00:00"),
    filing("2", "Outcome of Board Meeting", "2024-06-01T10:00:00"),
    filing("3", "Loss of Share Certificates", "2024-01-02T10:00:00"),
    filing("4", "Acquisition", "2024-02-01T10:00:00"),
    filing("5", "Outcome of Board Meeting", "2024-06-01T10:00:00", "https://a/2.pdf"),  # same file
    filing("6", "Press Release", "2024-01-01T10:00:00", url="https://a/6.pdf"),
]


class TestPriority:
    def test_material_first_then_the_rest_and_oldest_first_within_a_tier(self) -> None:
        urls = AttachmentPriority().order(FILINGS)

        assert urls == ["https://a/6.pdf", "https://a/4.pdf", "https://a/2.pdf", "https://a/1.pdf"]

    def test_routine_categories_are_skipped_unless_asked_for(self) -> None:
        assert "https://a/3.pdf" not in AttachmentPriority().order(FILINGS)
        assert AttachmentPriority(include_routine=True).order(FILINGS)[-1] == "https://a/3.pdf"

    def test_a_filing_without_an_attachment_is_not_in_the_list(self) -> None:
        bare = filing("7", "Acquisition", "2024-01-01T10:00:00")
        bare = Filing(**{**bare.__dict__, "attachment_url": ""})

        assert AttachmentPriority().order([bare]) == []

    def test_tiers(self) -> None:
        assert AttachmentPriority.tier("Acquisition") == 1
        assert AttachmentPriority.tier("Updates") == 2
        assert AttachmentPriority.tier("Trading Window") == 3


def text(url: str, status: str, hours: int = 48) -> AttachmentText:
    return AttachmentText(url, datetime(2024, 6, 3, 10, tzinfo=UTC), status, "body", 10, "h")


class TestReport:
    def lines(self) -> str:
        rows = EventBuilder({}).build(FILINGS, {})
        texts = {
            "https://a/2.pdf": text("https://a/2.pdf", "ok"),
            "https://a/1.pdf": text("https://a/1.pdf", "image_only"),
            "https://a/4.pdf": text("https://a/4.pdf", "ok"),
        }
        return "\n".join(filing_report_lines(rows, texts))

    def test_it_reports_months_categories_delays_and_the_image_only_share(self) -> None:
        out = self.lines()

        assert "6 events, 1 names" in out
        assert "2024-01" in out and "2024-06" in out
        assert "Outcome of Board Meeting" in out
        assert "median 0h00m10s" in out  # the company-to-exchange lag is the 10 seconds we built
        assert "image-only share of the PDFs read: 25.0% (1 of 4)" in out
        assert "attachments asked for: 4" in out

    def test_an_empty_store_says_so(self) -> None:
        assert filing_report_lines([], {}) == ["no events"]
