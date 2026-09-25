"""EM-239: is a tier of attachment text extracted yet."""

from __future__ import annotations

from datetime import UTC, date, datetime

from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.filing import Filing
from emporos.research.filings.priority import MATERIAL
from emporos.research.filings.progress import ExtractionProgress

MATERIAL_CATEGORY = sorted(MATERIAL)[0]
FIRST, LAST = date(2024, 1, 1), date(2024, 12, 31)
IDS = {"ABB": "NSE:13"}


def filing(
    seq: str, day: datetime, category: str = MATERIAL_CATEGORY, symbol: str = "ABB", url: str = ""
) -> Filing:
    return Filing(
        "NSE", symbol, "INE1", "Co", seq, day, None, category, "s",
        url or f"https://x/{seq}.pdf", "src", date(2026, 9, 25),
    )  # fmt: skip


def text(url: str, status: str) -> AttachmentText:
    return AttachmentText(url, datetime(2026, 9, 25, tzinfo=UTC), status, "", 0, "h")


def test_pending_until_every_material_attachment_has_a_record() -> None:
    day = datetime(2024, 3, 5, 4, 30, tzinfo=UTC)
    filings = [filing("1", day), filing("2", day)]
    held = {"https://x/1.pdf": text("https://x/1.pdf", "ok")}

    progress = ExtractionProgress.of(filings, held, IDS, FIRST, LAST)

    assert (progress.total, progress.pending, progress.done) == (2, 1, False)
    held["https://x/2.pdf"] = text("https://x/2.pdf", "not_found")
    done = ExtractionProgress.of(filings, held, IDS, FIRST, LAST)
    assert done.done and done.by_status == {"ok": 1, "not_found": 1}
    assert "2 of 2 read (0 pending)" in done.detail()


def test_scope_is_window_tier_name_and_attachment() -> None:
    day = datetime(2024, 3, 5, 4, 30, tzinfo=UTC)
    filings = [
        filing("1", datetime(2025, 1, 2, tzinfo=UTC)),  # outside the window
        filing("2", day, category="Some Routine Thing"),  # another tier
        filing("3", day, symbol="ZZZ"),  # no instrument id
        filing("4", day),
        filing("1", day),  # a duplicate id counts once with the first
        filing("1", day),
    ]
    progress = ExtractionProgress.of(filings, {}, IDS, FIRST, LAST)
    assert progress.total == 2  # ids 4 (url falls back) and 1


def test_an_empty_scope_is_not_done() -> None:
    assert not ExtractionProgress.of([], {}, IDS, FIRST, LAST).done
