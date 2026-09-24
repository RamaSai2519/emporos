"""The ledger's record mapping without a database: what goes in comes back out (EM-189)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporos.core.ids import IdGenerator
from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    TransitionKind,
)
from emporos.persistence.collections import Collection
from emporos.persistence.graduation_store import MongoGraduationLedger
from emporos.persistence.schema import PLATFORM_SCHEMA


class _Database:
    def __getitem__(self, name: str) -> Any:
        return object()


def test_an_event_survives_the_record_round_trip() -> None:
    ledger = MongoGraduationLedger(_Database(), IdGenerator())  # type: ignore[arg-type]
    event = GraduationEvent(
        "orb_v1", "abcdef0123456789", 3, GraduationStage.PAPER, GraduationStage.LIVE_CONSERVATIVE,
        TransitionKind.PROMOTE, (EvidenceRef(EvidenceKind.EXPERIMENT, "EXP-1", "accepted"),),
        "rama", "all gates green", datetime(2026, 9, 24, 4, 0, tzinfo=UTC),
    )  # fmt: skip

    record = ledger._record(event)

    assert record.seq == 3 and record.to_stage == "live_conservative"
    assert ledger._event(record) == event


def test_the_indexes_make_seq_and_acknowledgements_unique() -> None:
    events = PLATFORM_SCHEMA.spec_for(Collection.GRADUATION_EVENTS)
    acknowledgements = PLATFORM_SCHEMA.spec_for(Collection.LIVE_ACKNOWLEDGEMENTS)

    assert any(i.unique and [k for k, _ in i.keys] == ["strategy", "seq"] for i in events.indexes)
    assert any(
        i.unique and [k for k, _ in i.keys] == ["strategy", "behaviour_hash"]
        for i in acknowledgements.indexes
    )
