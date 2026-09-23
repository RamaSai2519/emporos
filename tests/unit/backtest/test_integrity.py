"""EM-177: `ResearchIntegrityGate` — a backtest must not silently cross a quarantined
corporate-action day."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from emporos.backtest.integrity import ResearchIntegrityGate, ResearchIntegrityViolation
from emporos.history.quarantine import CorporateActionQuarantine, QuarantineEntry, QuarantineSource

DAY1, DAY2, DAY3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
RECORDED = datetime(2026, 3, 5, 0, 0)


def entry(instrument_id: str, day: date) -> QuarantineEntry:
    return QuarantineEntry(instrument_id, day, "split", QuarantineSource.DETECTED, RECORDED)


def test_a_clean_window_passes() -> None:
    gate = ResearchIntegrityGate(CorporateActionQuarantine())

    gate.check(["NSE:1", "NSE:2"], DAY1, DAY3)  # does not raise


def test_a_window_crossing_a_quarantined_day_is_refused() -> None:
    gate = ResearchIntegrityGate(CorporateActionQuarantine([entry("NSE:1", DAY2)]))

    with pytest.raises(ResearchIntegrityViolation, match="NSE:1 2026-03-03"):
        gate.check(["NSE:1"], DAY1, DAY3)


def test_a_quarantined_day_for_an_instrument_not_requested_does_not_block() -> None:
    gate = ResearchIntegrityGate(CorporateActionQuarantine([entry("NSE:OTHER", DAY2)]))

    gate.check(["NSE:1"], DAY1, DAY3)  # does not raise


def test_allow_quarantined_is_an_explicit_opt_in_past_the_violation() -> None:
    gate = ResearchIntegrityGate(CorporateActionQuarantine([entry("NSE:1", DAY2)]))

    gate.check(["NSE:1"], DAY1, DAY3, allow_quarantined=True)  # does not raise


def test_every_hit_across_multiple_instruments_is_reported_together() -> None:
    gate = ResearchIntegrityGate(
        CorporateActionQuarantine([entry("NSE:1", DAY2), entry("NSE:2", DAY3)])
    )

    with pytest.raises(ResearchIntegrityViolation, match="2 quarantined"):
        gate.check(["NSE:1", "NSE:2"], DAY1, DAY3)
