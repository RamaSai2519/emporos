"""What the filing collection holds, in numbers (EM-239): events per month, the category mix, the
lag between an announcement and its exchange dissemination, the lag until its text was extracted,
and how many attachments are image-only (no OCR: marked, never silently empty)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from statistics import median

from emporos.core.clock import IST
from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.event_store import EventRow

__all__ = ["filing_report_lines"]


def _hms(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds >= 86_400:
        return f"{seconds / 86_400:.1f} days"
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m{int(seconds % 60):02d}s"


def filing_report_lines(
    rows: Iterable[EventRow], texts: Mapping[str, AttachmentText], top: int = 25
) -> list[str]:
    events = list(rows)
    if not events:
        return ["no events"]
    per_month = Counter(f"{r.published_at.astimezone(IST):%Y-%m}" for r in events)
    categories = Counter(r.category for r in events)
    lines = [
        f"{len(events)} events, {len({r.symbol for r in events})} names",
        "",
        "events per month:",
    ]
    lines += [f"  {month}  {count:>6}" for month, count in sorted(per_month.items())]
    lines += ["", f"category mix (top {top} of {len(categories)}):"]
    lines += [
        f"  {count:>6}  {count / len(events):>5.1%}  {name}"
        for name, count in categories.most_common(top)
    ]
    dissemination = [
        (r.published_at - r.submitted_at).total_seconds() for r in events if r.submitted_at
    ]
    if dissemination:
        lines += [
            "",
            "company filing time to exchange dissemination (the timestamp we use): median "
            f"{_hms(median(dissemination))}, 90th percentile "
            f"{_hms(sorted(dissemination)[int(0.9 * len(dissemination))])}",
        ]
    held = [(r, texts[r.attachment_url]) for r in events if r.attachment_url in texts]
    by_status = Counter(t.status for _, t in held)
    lines += [
        "",
        f"attachments asked for: {len(held)} of {sum(1 for r in events if r.attachment_url)}",
    ]
    lines += [
        f"  {status:<11} {count:>6}  {count / max(len(held), 1):>5.1%}"
        for status, count in sorted(by_status.items())
    ]
    extracted = [
        (t.fetched_at - r.published_at).total_seconds() for r, t in held if t.status == "ok"
    ]
    if extracted:
        lines += [
            "",
            f"median delay from dissemination to text extraction: {_hms(median(extracted))} "
            "(a historical backfill: this is when we collected, not a live latency)",
        ]
    pdf_answers = by_status["ok"] + by_status["image_only"]
    if pdf_answers:
        lines.append(
            f"image-only share of the PDFs read: {by_status['image_only'] / pdf_answers:.1%} "
            f"({by_status['image_only']} of {pdf_answers}); no OCR, marked text_status=image_only"
        )
    return lines
