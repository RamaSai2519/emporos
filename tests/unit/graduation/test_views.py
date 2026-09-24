"""The launch gate's two views over the ledger and the acknowledgement book (EM-189)."""

from __future__ import annotations

from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    LiveAcknowledgement,
    TransitionKind,
    acknowledgement_phrase,
)
from emporos.graduation.views import BookAcknowledgementView, LedgerStageView
from tests.support.graduation import HASH, NOW, STRATEGY, MemoryAcknowledgements, MemoryLedger


async def test_the_stage_view_reads_the_ledger_for_this_configuration_only() -> None:
    ledger = MemoryLedger()
    view = LedgerStageView(ledger)
    assert await view.stage(STRATEGY, HASH) is GraduationStage.RESEARCH

    await ledger.append(
        GraduationEvent(
            STRATEGY,
            HASH,
            1,
            GraduationStage.RESEARCH,
            GraduationStage.PAPER,
            TransitionKind.PROMOTE,
            (EvidenceRef(EvidenceKind.VERDICT, "v"),),
            "rama",
            "ok",
            NOW,
        )  # fmt: skip
    )

    assert await view.stage(STRATEGY, HASH) is GraduationStage.PAPER
    assert await view.stage(STRATEGY, "0" * 16) is GraduationStage.RESEARCH  # edited: stale


async def test_the_acknowledgement_view_is_per_strategy_and_configuration() -> None:
    ack = LiveAcknowledgement(
        STRATEGY, HASH, "rama", acknowledgement_phrase(STRATEGY, HASH), "live_conservative", NOW
    )
    view = BookAcknowledgementView(MemoryAcknowledgements(ack))

    assert await view.acknowledged(STRATEGY, HASH) is True
    assert await view.acknowledged(STRATEGY, "0" * 16) is False
    assert await view.acknowledged("other", HASH) is False
