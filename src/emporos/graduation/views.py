"""The ledger and the acknowledgement book, seen the way the launch gate wants them.

`session.launch_gate` cannot import graduation (the layering runs the other way), so it declares the
two tiny views it needs and these adapters satisfy them structurally.
"""

from __future__ import annotations

from emporos.domain.graduation import GraduationStage, effective_stage
from emporos.graduation.ports import AcknowledgementBook, GraduationLedger


class LedgerStageView:
    def __init__(self, ledger: GraduationLedger) -> None:
        self._ledger = ledger

    async def stage(self, strategy: str, behaviour_hash: str) -> GraduationStage:
        return effective_stage(await self._ledger.latest(strategy), behaviour_hash)


class BookAcknowledgementView:
    def __init__(self, book: AcknowledgementBook) -> None:
        self._book = book

    async def acknowledged(self, strategy: str, behaviour_hash: str) -> bool:
        return await self._book.get(strategy, behaviour_hash) is not None
