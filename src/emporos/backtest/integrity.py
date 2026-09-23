"""Research-integrity gate (EM-177): invariants a backtest must not silently violate.

Structural, not advisory: `BacktestJob` and `CurationRun` call `check` before running, and a
violation raises rather than merely logging a warning — the "never downgrade a guarantee" rule in
AGENTS.md applies to research data exactly as it does to order placement.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from emporos.core.errors import DefinitiveError
from emporos.history.quarantine import CorporateActionQuarantine, QuarantineEntry


class ResearchIntegrityViolation(DefinitiveError):
    """A backtest's requested instruments/date range violates a research-integrity invariant."""


class ResearchIntegrityGate:
    def __init__(self, quarantine: CorporateActionQuarantine) -> None:
        self._quarantine = quarantine

    def check(
        self,
        instrument_ids: Iterable[str],
        first: date,
        last: date,
        *,
        allow_quarantined: bool = False,
    ) -> None:
        """Raise if any instrument's window overlaps a quarantined corporate-action day.

        `allow_quarantined=True` is the caller's explicit, recorded opt-in (mirroring
        `assume_current_universe`) — never a silent default."""
        if allow_quarantined:
            return
        hits = sorted(
            (
                entry
                for instrument_id in instrument_ids
                for entry in self._quarantine.overlapping(instrument_id, first, last)
            ),
            key=lambda e: (e.instrument_id, e.day),
        )
        if hits:
            raise ResearchIntegrityViolation(self._message(hits, first, last))

    @staticmethod
    def _message(hits: list[QuarantineEntry], first: date, last: date) -> str:
        described = ", ".join(f"{e.instrument_id} {e.day.isoformat()}" for e in hits)
        return (
            f"{len(hits)} quarantined corporate-action artifact(s) fall inside "
            f"{first.isoformat()}..{last.isoformat()}: {described}. An unadjusted split, bonus or "
            "confirmed bad print must not be traded across silently (EM-177); pass "
            "allow_quarantined=True only after a reviewed adjustment."
        )
