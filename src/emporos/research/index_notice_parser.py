"""Read NIFTY 50, NIFTY Next 50, NIFTY 100 and NIFTY Midcap 150 changes out of a notice (EM-224).

An NSE Indices press release (as `pdftotext -layout` gives it) is regular: a paragraph saying the
changes are "effective from <date>", then numbered sections, one per index ("1)  NIFTY 50"), each
with "The following company is being excluded:" and/or "... included:" and a table whose rows end in
the symbol. This module reads exactly that and nothing cleverer:

* a section belongs to the effective date in the nearest "effective from" / "w.e.f." phrase before
  it (a notice can carry several, one per lettered block);
* only sections named NIFTY 50, NIFTY Next 50, NIFTY 100 or NIFTY Midcap 150 are kept (the name
  is matched with spaces and case ignored: "NIFTY Midcap150", "Nifty100");
* a section with a name we want but no rows, or a notice that mentions one of these indices and
  yields no section, is REPORTED (`unread`) and never guessed at.

`ChangeBuilder` turns sections into `IndexChange`s for the two indices D1 is made of: NIFTY 100
(the union of NIFTY 50 and NIFTY Next 50, so a name moving between them nets out) and NIFTY MIDCAP
150. The result is only as good as the notices are complete; `MembershipTimeline` checks it against
today's constituent lists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from emporos.research.index_membership import IndexChange

__all__ = ["ChangeBuilder", "NoticeParser", "ParsedNotice", "Section", "SectionKind"]

_DATE = r"([A-Z][a-z]{2,8}\.? ?\d{1,2},? ?\d{4})"
_EFFECTIVE = re.compile(
    r"(?:effective\s+from|w\.e\.f\.?|effective\s+on)\s*(?:the\s+)?" + _DATE, re.I
)
_HEADING = re.compile(r"^\s*(\d{1,2})\)\s+(.+?)\s*$", re.M)
_MODE = re.compile(r"being\s+(excluded|included)\s*:", re.I)
_THEMATIC = re.compile(
    r"quality|alpha|value|volatil|liquid|momentum|equalweight|growth|dividend|lowvol|sector|leader"
)
_LOOSE = re.compile(r"^(?:nifty(?:50|100)(?:and|&)|.*(?:midcap150|next50))")
_ROW = re.compile(r"^\s*\d+\s+.+?\s{2,}([A-Z0-9][A-Z0-9&\-]*)\s*$")


class SectionKind(StrEnum):
    NIFTY_50 = "nifty50"
    NEXT_50 = "niftynext50"
    NIFTY_100 = "nifty100"
    MIDCAP_150 = "niftymidcap150"


@dataclass(frozen=True)
class Section:
    kind: SectionKind
    effective: date | None
    excluded: tuple[str, ...]
    included: tuple[str, ...]


@dataclass(frozen=True)
class ParsedNotice:
    name: str
    sections: tuple[Section, ...]
    unread: tuple[str, ...]  # what could not be read, and why


def _parse_date(text: str) -> date | None:
    cleaned = re.sub(r"[.,]", " ", text)
    cleaned = " ".join(cleaned.split())
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _kind(name: str) -> SectionKind | None:
    squashed = re.sub(r"[^a-z0-9]", "", name.lower()).removesuffix("index")
    for kind in SectionKind:
        if squashed == kind.value:
            return kind
    return None


class NoticeParser:
    def parse(self, name: str, text: str) -> ParsedNotice:
        effective_at = [(m.start(), _parse_date(m.group(1))) for m in _EFFECTIVE.finditer(text)]
        headings = list(_HEADING.finditer(text))
        sections: list[Section] = []
        unread: list[str] = []
        for i, heading in enumerate(headings):
            kind = _kind(heading.group(2))
            if kind is None:
                squashed = re.sub(r"[^a-z0-9]", "", heading.group(2).lower())
                if _LOOSE.search(squashed) and not _THEMATIC.search(squashed):
                    unread.append(
                        f"{heading.group(2).strip()}: a heading that names one of ours, unread"
                    )
                continue
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[heading.end() : end]
            excluded, included = self._rows(body)
            preceding = [d for pos, d in effective_at if pos < heading.start() and d is not None]
            effective = preceding[-1] if preceding else None
            if not (excluded or included):
                unread.append(f"{heading.group(2).strip()}: a section with no rows")
                continue
            if effective is None:
                unread.append(f"{heading.group(2).strip()}: no effective date before it")
            sections.append(Section(kind, effective, tuple(excluded), tuple(included)))
        return ParsedNotice(name, tuple(sections), tuple(unread))

    @staticmethod
    def _rows(body: str) -> tuple[list[str], list[str]]:
        excluded: list[str] = []
        included: list[str] = []
        target: list[str] | None = None
        for line in body.splitlines():
            mode = _MODE.search(line)
            if mode:
                target = excluded if mode.group(1).lower() == "excluded" else included
                continue
            row = _ROW.match(line)
            if row and target is not None:
                target.append(row.group(1))
        return excluded, included


class ChangeBuilder:
    """Sections to `IndexChange`s. NIFTY 100 is rebuilt from NIFTY 50 and NIFTY Next 50 (an explicit
    NIFTY 100 section is used only when the notice has neither of those), so a promotion from the
    Next 50 to the 50 is not a change to NIFTY 100."""

    def build(
        self, notices: Iterable[ParsedNotice], sources: Mapping[str, str]
    ) -> tuple[list[IndexChange], list[str]]:
        changes: list[IndexChange] = []
        problems: list[str] = []
        for notice in notices:
            by_date: dict[date | None, list[Section]] = {}
            for section in notice.sections:
                by_date.setdefault(section.effective, []).append(section)
            for effective, sections in by_date.items():
                if effective is None:
                    problems.append(f"{notice.name}: sections without an effective date skipped")
                    continue
                source = sources.get(notice.name, notice.name)
                changes.extend(self._changes(effective, sections, source))
        return sorted(changes, key=lambda c: (c.effective, c.index)), problems

    def _changes(
        self, effective: date, sections: Sequence[Section], source: str
    ) -> list[IndexChange]:
        out: list[IndexChange] = []
        components = [s for s in sections if s.kind in (SectionKind.NIFTY_50, SectionKind.NEXT_50)]
        explicit = [s for s in sections if s.kind is SectionKind.NIFTY_100]
        n100 = components or explicit
        out += self._one("NIFTY 100", effective, n100, source)
        out += self._one(
            "NIFTY MIDCAP 150", effective,
            [s for s in sections if s.kind is SectionKind.MIDCAP_150], source,
        )  # fmt: skip
        return out

    @staticmethod
    def _one(
        index: str, effective: date, sections: Sequence[Section], source: str
    ) -> list[IndexChange]:
        if not sections:
            return []
        included = {n for s in sections for n in s.included}
        excluded = {n for s in sections for n in s.excluded}
        added, removed = included - excluded, excluded - included  # a name in both nets out
        if not (added or removed):
            return []
        return [IndexChange(index, effective, frozenset(added), frozenset(removed), source)]
