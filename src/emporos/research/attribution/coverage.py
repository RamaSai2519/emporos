"""What the Cause Ledger does NOT hold, stated in every prompt (docs/research/profit/
cause-ledger-coverage.md, EM-245): a model that is not told an item class is absent may read the
absence as "nothing happened"."""

from __future__ import annotations

from dataclasses import dataclass, field

from emporos.research.attribution.items import ItemClass

__all__ = ["Coverage", "NO_HEADLINES"]

NO_HEADLINES = "No headlines are available."
_ABSENT_TEXT = {
    ItemClass.HEADLINE: NO_HEADLINES,
    ItemClass.FLOW: "FII/DII cash flows are not available.",
    ItemClass.BULK_DEAL: "Bulk and block deals are not available.",
}


@dataclass(frozen=True)
class Coverage:
    """The item classes the ledger holds; the others are absent and each says so."""

    absent: frozenset[ItemClass] = field(
        default_factory=lambda: frozenset({ItemClass.HEADLINE, ItemClass.FLOW, ItemClass.BULK_DEAL})
    )

    def lines(self) -> list[str]:
        return [_ABSENT_TEXT[c] for c in ItemClass if c in self.absent and c in _ABSENT_TEXT]
