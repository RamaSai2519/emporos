"""What the committed history audit says about the data (EM-191, docs/data/history-quality.json).

Two facts a study needs and must not guess: which instruments have the whole ten years (the
audit's spans), and which instrument-days are quarantined unadjusted corporate actions (its
`overnight_discontinuity` findings). A split posing as a 90% gap would be the best "gap edge" ever
found, so a gap study drops those days. Reads the committed JSON: no database.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emporos.core.errors import ConfigurationError

__all__ = ["DEFAULT_AUDIT_FILE", "HistoryAudit"]

DEFAULT_AUDIT_FILE = Path("docs/data/history-quality.json")
_DISCONTINUITY = "overnight_discontinuity"


@dataclass(frozen=True)
class HistoryAudit:
    instrument_ids: tuple[str, ...]  # the names with the audited span, in audit order
    quarantined: frozenset[tuple[str, date]]  # (instrument id, IST day)
    first: date
    last: date

    @classmethod
    def load(cls, path: Path = DEFAULT_AUDIT_FILE) -> HistoryAudit:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                tuple(str(s["instrument"]) for s in document["spans"]),
                frozenset(
                    (str(f["instrument"]), date.fromisoformat(f["day"]))
                    for f in document["findings"]
                    if f["check"] == _DISCONTINUITY
                ),
                date.fromisoformat(document["first"]),
                date.fromisoformat(document["last"]),
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigurationError(f"{path} is not a history audit: {error}") from error

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) in self.quarantined

    @property
    def universe_label(self) -> str:
        """A short stable name for this universe, for a screen's identity."""
        digest = hashlib.sha256(",".join(sorted(self.instrument_ids)).encode()).hexdigest()
        return f"audit{len(self.instrument_ids)}-{digest[:8]}"
