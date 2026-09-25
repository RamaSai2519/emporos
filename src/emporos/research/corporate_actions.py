"""Corporate actions as public records, and how they become adjustment factors (EM-221, A-F2).

`CorporateAction` is one line of the exchange's corporate-actions feed: a symbol, an ex-date, and a
free-text subject ("Bonus 1:1", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/-
Per Share", "Dividend - Rs 6 Per Share"). `SubjectInterpreter` reads a subject and says one of three
things and never guesses:

* it is a SPLIT, BONUS or CONSOLIDATION with a ratio the text states: a factor;
* it moves the price basis in a way the text does not turn into a ratio (a rights issue, a
  demerger, an amalgamation, a capital reduction, or a split or bonus whose wording did not parse):
  it goes to the REVIEW list, unadjusted, so the audit shows what is left rather than hiding it;
* it is cash (a dividend, an interest payment, a meeting): ignored, a known limit of price bars.

A bonus `A:B` is A new shares for every B held, so the price multiplier is B/(A+B): 1:1 is 0.5 and
1:2 is 2/3. A face-value change from `old` to `new` multiplies the price by new/old.

`ActionReader` turns the recorded actions into the ratios their text states, per instrument. It does
NOT decide where a ratio applies: the broker's daily history turns out to be already adjusted for
most actions (see `gap_matching`), so applying an exchange ex-date to it would double-adjust.

`CorporateActionLedger` keeps every record with its source URL and fetch date, append-only, once
each, plus a mark per collected symbol, so an interrupted run resumes and nothing is asked twice.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from emporos.research.adjustments import ActionKind

__all__ = [
    "ActionReader", "CorporateAction", "CorporateActionLedger", "Interpretation", "ReadAction",
    "ReadActions", "SubjectInterpreter", "parse_actions",
]  # fmt: skip

_EX_DATE = "%d-%b-%Y"
_BONUS = re.compile(r"\bbonus\b[^0-9]*(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_FACE_VALUE = re.compile(
    r"from\s+r[se]?\.?\s*([\d.]+)\s*/?-?\s*per\s+share\s+to\s+r[se]?\.?\s*([\d.]+)",
    re.IGNORECASE,
)
_PRICE_AFFECTING = re.compile(
    r"rights|demerger|de-merger|amalgamation|arrangement|capital reduction|spin[- ]?off|"
    r"split|sub-?division|consolidation|bonus|merger",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: date
    subject: str
    isin: str = ""
    series: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.symbol, self.ex_date.isoformat(), self.subject)


@dataclass(frozen=True)
class Interpretation:
    factors: tuple[tuple[ActionKind, Decimal], ...]  # (kind, ratio) read from the text
    needs_review: bool  # price-affecting but not turned into a ratio


class SubjectInterpreter:
    def interpret(self, subject: str) -> Interpretation:
        factors: list[tuple[ActionKind, Decimal]] = []
        bonus = _BONUS.search(subject)
        if bonus:
            new, held = Decimal(bonus.group(1)), Decimal(bonus.group(2))
            if new > 0 and held > 0:
                factors.append((ActionKind.BONUS, held / (new + held)))
        face = _FACE_VALUE.search(subject)
        if face:
            try:
                old, new_face = Decimal(face.group(1)), Decimal(face.group(2))
            except InvalidOperation:
                old = new_face = Decimal(0)
            if old > 0 and new_face > 0 and old != new_face:
                kind = ActionKind.SPLIT if new_face < old else ActionKind.OTHER
                factors.append((kind, new_face / old))
        matched_all = bool(factors) and self._wholly_read(subject, bonus, face)
        return Interpretation(
            tuple(factors), needs_review=bool(_PRICE_AFFECTING.search(subject)) and not matched_all
        )

    @staticmethod
    def _wholly_read(subject: str, bonus: re.Match[str] | None, face: re.Match[str] | None) -> bool:
        """True when nothing price-affecting is left unread once the parsed parts are removed."""
        rest = subject
        for match in (bonus, face):
            if match:
                rest = rest.replace(match.group(0), " ")
        return not re.search(
            r"rights|demerger|de-merger|amalgamation|arrangement|capital reduction|spin|merger",
            rest,
            re.IGNORECASE,
        )


def parse_actions(payload: object, symbol: str) -> list[CorporateAction]:
    """The feed's JSON array to actions. A record without a usable ex-date is skipped: an action
    with no date cannot adjust anything. Anything that is not a list of objects is an error."""
    if not isinstance(payload, list):
        raise ValueError(f"{symbol}: the corporate-actions feed did not answer with a list")
    actions: list[CorporateAction] = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError(f"{symbol}: a corporate-actions row is not an object: {row!r}")
        try:
            ex_date = datetime.strptime(str(row["exDate"]), _EX_DATE).date()
        except (KeyError, ValueError):
            continue
        actions.append(
            CorporateAction(
                symbol, ex_date, " ".join(str(row.get("subject", "")).split()),
                str(row.get("isin", "")), str(row.get("series", "")),
            )
        )  # fmt: skip
    return sorted(actions, key=lambda a: (a.ex_date, a.subject))


class CorporateActionLedger:
    """Two append-only JSONL files: the records (each once, with provenance) and the marks."""

    def __init__(self, records: Path, collected: Path) -> None:
        self._records = records
        self._collected = collected

    def collected_symbols(self) -> frozenset[str]:
        return frozenset(str(row["symbol"]) for row in self._read(self._collected))

    def actions(self) -> list[CorporateAction]:
        return [
            CorporateAction(
                str(r["symbol"]), date.fromisoformat(str(r["ex_date"])), str(r["subject"]),
                str(r.get("isin", "")), str(r.get("series", "")),
            )
            for r in self._read(self._records)
        ]  # fmt: skip

    def record(
        self,
        symbol: str,
        actions: Sequence[CorporateAction],
        first: date,
        last: date,
        fetched_at: datetime,
        source_url: str,
    ) -> int:
        """Append the actions not already held, then mark `symbol` collected; the number added."""
        have = {(r["symbol"], r["ex_date"], r["subject"]) for r in self._read(self._records)}
        fresh = [a for a in actions if a.key not in have]
        self._append(
            self._records,
            [
                {
                    "symbol": a.symbol, "ex_date": a.ex_date.isoformat(), "subject": a.subject,
                    "isin": a.isin, "series": a.series, "source_url": source_url,
                    "fetched_on": fetched_at.date().isoformat(),
                }
                for a in dict.fromkeys(fresh)
            ],
        )  # fmt: skip
        self._append(
            self._collected,
            [
                {
                    "symbol": symbol, "from": first.isoformat(), "to": last.isoformat(),
                    "source_url": source_url, "fetched_on": fetched_at.date().isoformat(),
                    "count": len(actions),
                }
            ],
        )  # fmt: skip
        return len(fresh)

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @staticmethod
    def _append(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        lines = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        if not lines:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(lines)


@dataclass(frozen=True)
class ReadAction:
    """A split, bonus or consolidation whose ratio the exchange's text states."""

    ex_date: date
    kind: ActionKind
    ratio: Decimal
    subject: str


@dataclass(frozen=True)
class ReadActions:
    by_instrument: Mapping[str, tuple[ReadAction, ...]]  # oldest ex-date first
    review: tuple[CorporateAction, ...]  # price-affecting, no ratio read: not adjusted
    ignored: int  # cash actions


class ActionReader:
    """Recorded actions and a symbol-to-instrument map to the ratios the text states."""

    def __init__(self, interpreter: SubjectInterpreter | None = None) -> None:
        self._interpreter = interpreter or SubjectInterpreter()

    def read(
        self, actions: Iterable[CorporateAction], instruments: Mapping[str, str]
    ) -> ReadActions:
        found: dict[str, dict[tuple[date, Decimal, ActionKind], ReadAction]] = {}
        review: list[CorporateAction] = []
        ignored = 0
        for action in actions:
            instrument_id = instruments.get(action.symbol)
            if instrument_id is None:
                continue
            reading = self._interpreter.interpret(action.subject)
            for kind, ratio in reading.factors:
                if ratio != 1:
                    key = (action.ex_date, ratio, kind)
                    found.setdefault(instrument_id, {})[key] = ReadAction(
                        action.ex_date, kind, ratio, action.subject
                    )
            if reading.needs_review:
                review.append(action)
            elif not reading.factors:
                ignored += 1
        ordered = {
            i: tuple(sorted(v.values(), key=lambda a: (a.ex_date, a.ratio)))
            for i, v in found.items()
        }
        return ReadActions(ordered, tuple(review), ignored)
