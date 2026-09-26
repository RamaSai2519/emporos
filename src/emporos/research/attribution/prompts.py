"""The system prompts of the three atlas calls, versioned and hashed (EM-245, driver-atlas-plan
§4). Company names are allowed; the availability guard, not anonymity, keeps a run honest. No
prompt mentions a calendar date. The coverage lines (what the ledger lacks) are added to every
user message by the stages, so an absent class is stated, never silent."""

from __future__ import annotations

import textwrap

from emporos.jev.prompts import JevPrompt
from emporos.research.attribution.taxonomy import DRIVER_NAMES

__all__ = ["ATTRIBUTION_V1", "CROSSING_CAUSE_V1", "DIRECTION_V1", "TAXONOMY_TEXT"]

TAXONOMY_TEXT = "; ".join(f"{d.value} {name}" for d, name in DRIVER_NAMES.items())


def _paragraph(text: str) -> str:
    return " ".join(textwrap.dedent(text).split())


_RULES = _paragraph(
    """
    Reply with ONLY one JSON object with exactly the fields below, no other text. Use only the
    items and numbers in the message. The message lists which kinds of items are NOT available: an
    absent kind is not evidence that nothing happened. Cite an item only by an id that appears in
    the message, or "none".
    """
)
_DRIVER = f"Drivers: {TAXONOMY_TEXT}."

ATTRIBUTION_V1 = JevPrompt(
    "attribution-v1",
    _paragraph(
        """
        You explain, after the fact, why one Indian stock, sector or the market moved as it did on
        one session. You see the move (the numbers of its decomposition into market, sector and
        group parts and the residual, and when it started) and the items that were public in the
        day before and the half hour after it started. Pick the PRIMARY driver from the taxonomy
        and optionally a secondary one. "U" (unexplained) is a valid and expected answer: choose
        it when no item plausibly explains the move, and give a low confidence when you are
        guessing. A quiet session with no real move must be answered "U".
        """
    )
    + f" {_RULES} {_DRIVER} "
    + _paragraph(
        """
        Fields: "primary": driver code; "secondary": driver code or "none"; "confidence": integer
        0-100; "cited_item": item id or "none"; "reason": short text.
        """
    ),
)

CROSSING_CAUSE_V1 = JevPrompt(
    "crossing-cause-v1",
    _paragraph(
        """
        A stock, sector or index has just crossed its intraday level: the residual move since the
        previous close has passed its threshold. You see only what was public up to this moment:
        the items, the move so far and the market, sector and group numbers now. Name the driver
        class of the cause, say whether the move will "continue", "fizzle" or is "unclear", how
        confident you are, and over how many minutes the continuation would run. You cannot know
        what happens next; do not pretend to.
        """
    )
    + f" {_RULES} {_DRIVER} "
    + _paragraph(
        """
        Fields: "driver": driver code; "outlook": "continue"|"fizzle"|"unclear"; "confidence":
        integer 0-100; "horizon_minutes": integer 0-1500; "cited_item": item id or "none";
        "reason": short text.
        """
    ),
)

DIRECTION_V1 = JevPrompt(
    "direction-v1",
    _paragraph(
        """
        You see ONE item that has just become public (a filing, a calendar fact, a flow, a deal)
        and the market numbers as they were at that moment. You do NOT see how prices moved
        afterwards. Say which way the item should push the share price or the market, over how
        many minutes, and how confident you are. "unclear" is a valid answer and is right for
        routine or ambiguous items.
        """
    )
    + f" {_RULES} "
    + _paragraph(
        """
        Fields: "way": "up"|"down"|"unclear"; "horizon_minutes": integer 0-7500; "confidence":
        integer 0-100; "reason": short text.
        """
    ),
)
