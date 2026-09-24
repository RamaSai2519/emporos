"""Composition root for graduation: the only place its ports are bound to Mongo (EM-189).

The launch gate, the worker and the `emporos graduation` commands all read the SAME ledger and
acknowledgement book through here, so there is one answer to "what stage is this configuration at".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.graduation.views import BookAcknowledgementView, LedgerStageView
from emporos.persistence.graduation_store import MongoAcknowledgementBook, MongoGraduationLedger
from emporos.risk.config import RiskTier
from emporos.session.launch_gate import GraduationStageView, LiveGraduation

Database = AsyncDatabase[Mapping[str, Any]]

# What a live worker trades under today. PRODUCTION is not promotable, so there is no other tier.
LIVE_RISK_TIER = RiskTier.LIVE_CONSERVATIVE


def stage_view(database: Database) -> GraduationStageView:
    return LedgerStageView(MongoGraduationLedger(database, IdGenerator()))


def live_graduation(database: Database, risk_tier: RiskTier = LIVE_RISK_TIER) -> LiveGraduation:
    return LiveGraduation(
        stages=stage_view(database),
        acknowledgements=BookAcknowledgementView(MongoAcknowledgementBook(database, IdGenerator())),
        risk_tier=risk_tier,
    )
