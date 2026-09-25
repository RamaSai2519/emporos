"""Which attachments to fetch first (PROFIT_PLAN §12.2, §8, EM-239).

Every attachment is a request three seconds apart, so about fifty thousand of them are a day of
wall-clock. The model needs the text of what can move a price, so attachments are fetched in three
tiers, and inside a tier oldest first (the Dev window 2024 is built first):

1. **Material:** results and board-meeting outcomes, acquisitions, agreements, mergers and schemes,
   credit ratings, management, director and auditor changes, dividends, press releases, investor
   presentations, business updates, orders and litigation, rumour/news verification, takeover
   disclosures, securities allotments and issues.
2. **Everything not listed:** the exchange's generic "Updates" and "General Updates" and any
   category not named here.
3. **Routine, skipped unless asked:** share-certificate losses, newspaper copies, trading-window
   notices, depository certificates, shareholder meeting notices, ESOP filings, record dates,
   monitoring-agency reports, deviation statements, and analyst-meet and call intimations.

The feed's own one-line text is in every event regardless; the tiers only rank the PDF fetches."""

from __future__ import annotations

from collections.abc import Iterable

from emporos.research.filings.filing import Filing

__all__ = ["AttachmentPriority", "MATERIAL", "ROUTINE"]

MATERIAL = frozenset(
    {
        "Outcome of Board Meeting", "Financial Result Updates", "Integrated Filing- Financial",
        "Acquisition", "Agreements", "Amalgamation/Merger", "Scheme of Arrangement",
        "Credit Rating", "Credit Rating- New", "Credit Rating- Revision", "Credit Rating- Others",
        "Change in Management", "Change in Director(s)", "Change in Auditors", "Resignation",
        "Appointment", "Cessation", "Dividend", "Dividend Updates", "Press Release",
        "Investor Presentation", "Monthly Business Updates", "News Verification",
        "Rumour Verification - Regulation 30(11)", "Disclosure under SEBI Takeover Regulations",
        "Allotment of Securities", "Issue of Securities", "Action(s) initiated or orders passed",
        "Action(s) taken or orders passed",
        "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company",
        "Incorporation", "Amendment to AOA/MOA", "Bagging/Receiving of Orders/Contracts",
        "Award of Order / Receipt of Order",
    }
)  # fmt: skip
ROUTINE = frozenset(
    {
        "Loss of Share Certificates", "Copy of Newspaper Publication", "Trading Window",
        "Certificate under SEBI (Depositories and Participants) Regulations, 2018",
        "Shareholders meeting", "ESOP/ESOS/ESPS", "Record Date", "Monitoring Agency Report",
        "Statement of deviation(s) or variation(s) under Reg. 32",
        "Analysts/Institutional Investor Meet/Con. Call Updates",
    }
)  # fmt: skip


class AttachmentPriority:
    def __init__(self, include_routine: bool = False) -> None:
        self._include_routine = include_routine

    @staticmethod
    def tier(category: str) -> int:
        if category in MATERIAL:
            return 1
        return 3 if category in ROUTINE else 2

    def order(self, filings: Iterable[Filing]) -> list[str]:
        """The attachment URLs to fetch, best first, each once."""
        ranked = sorted(
            (
                (self.tier(f.category), f.published_at, f.attachment_url)
                for f in filings
                if f.attachment_url and (self._include_routine or self.tier(f.category) < 3)
            ),
        )
        seen: set[str] = set()
        out: list[str] = []
        for _, _, url in ranked:
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out
