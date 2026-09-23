"""EM-177: `CorporateActionQuarantine` and `QuarantineCurator`."""

from __future__ import annotations

from datetime import date, datetime

from emporos.history.quarantine import (
    CorporateActionQuarantine,
    QuarantineCurator,
    QuarantineEntry,
    QuarantineSource,
)

DAY1, DAY2, DAY3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
RECORDED = datetime(2026, 3, 5, 0, 0)


def entry(
    instrument_id: str, day: date, source: QuarantineSource = QuarantineSource.DETECTED
) -> QuarantineEntry:
    reason = "opened at 0.5x the previous close"
    return QuarantineEntry(instrument_id, day, reason, source, RECORDED)


def test_an_empty_quarantine_flags_nothing() -> None:
    quarantine = CorporateActionQuarantine()

    assert not quarantine.is_quarantined("NSE:1", DAY1)
    assert quarantine.overlapping("NSE:1", DAY1, DAY3) == ()
    assert len(quarantine) == 0


def test_is_quarantined_is_keyed_by_instrument_and_day() -> None:
    quarantine = CorporateActionQuarantine([entry("NSE:1", DAY2)])

    assert quarantine.is_quarantined("NSE:1", DAY2)
    assert not quarantine.is_quarantined("NSE:1", DAY1)
    assert not quarantine.is_quarantined("NSE:2", DAY2)


def test_overlapping_returns_every_entry_for_the_instrument_inside_the_window() -> None:
    quarantine = CorporateActionQuarantine(
        [entry("NSE:1", DAY1), entry("NSE:1", DAY3), entry("NSE:2", DAY2)]
    )

    found = quarantine.overlapping("NSE:1", DAY1, DAY2)

    assert [e.day for e in found] == [DAY1]


def test_the_curator_proposes_only_findings_not_already_quarantined() -> None:
    existing = CorporateActionQuarantine([entry("NSE:1", DAY1)])
    findings = [("NSE:1", DAY1, "old"), ("NSE:1", DAY2, "new"), ("NSE:2", DAY3, "also new")]

    proposed = QuarantineCurator().propose(findings, existing, RECORDED)

    assert [(e.instrument_id, e.day) for e in proposed] == [("NSE:1", DAY2), ("NSE:2", DAY3)]
    assert all(e.source is QuarantineSource.DETECTED for e in proposed)
    assert all(e.recorded_at == RECORDED for e in proposed)


def test_the_curator_proposes_nothing_new_once_everything_is_quarantined() -> None:
    existing = CorporateActionQuarantine([entry("NSE:1", DAY1)])

    assert QuarantineCurator().propose([("NSE:1", DAY1, "x")], existing, RECORDED) == ()
