from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.core.ids import IdGenerator
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.jev.models import CONFIRM, JevDecision
from emporos.opportunity.allocator import Allocation
from emporos.opportunity.audit import OpportunityAuditLog
from emporos.opportunity.jev_filter import JevFilterResult, JevReview
from emporos.opportunity.models import OpportunityCandidate, RejectedCandidate
from emporos.opportunity.pipeline import BarOutcome
from emporos.opportunity.scanner import ScanResult
from emporos.persistence.records import OpportunityScanRecord
from emporos.strategies.regime import MarketRegime
from tests.support.strategies import INSTRUMENT, T0, make_signal

DECIDED_AT = datetime(2026, 1, 5, 3, 46, tzinfo=UTC)


class _FakeStore:
    def __init__(self) -> None:
        self.inserted: list[OpportunityScanRecord] = []

    async def insert(self, record: OpportunityScanRecord) -> None:
        self.inserted.append(record)


def _candidate(instrument_id: str = INSTRUMENT) -> OpportunityCandidate:
    return OpportunityCandidate(
        strategy_name="momentum_v1",
        instrument_id=instrument_id,
        timeframe=Timeframe.M5,
        signal=make_signal(instrument_id=instrument_id, price="100"),
        entry=Money.of("100"),
        stop=Money.of("98"),
        target=Money.of("104"),
        regime=MarketRegime.TRENDING,
        generated_at=T0,
    )


def _decision() -> JevDecision:
    return JevDecision(
        decision=CONFIRM,
        confidence=Decimal("0.8"),
        provider="test",
        model="test-model",
        config_version=None,
        requested_at=DECIDED_AT,
        latency_ms=15,
        tokens_used=50,
    )


async def test_a_full_outcome_is_recorded_with_every_section() -> None:
    candidate = _candidate()
    outcome = BarOutcome(
        ts=T0,
        exits=(),
        scan=ScanResult(
            candidates=(candidate,),
            rejected=(
                RejectedCandidate(
                    strategy_name="other", instrument_id=INSTRUMENT, reason="not eligible", ts=T0
                ),
            ),
        ),
        jev=JevFilterResult(
            candidates=(candidate,),
            reviews=(JevReview(candidate, _decision()),),
            rejected=(),
        ),
        allocations=(Allocation(candidate, 10),),
    )
    store = _FakeStore()
    log = OpportunityAuditLog(store, IdGenerator())

    await log.record(outcome)

    assert len(store.inserted) == 1
    record = store.inserted[0]
    assert record.ts == T0
    assert record.instrument_ids == [INSTRUMENT]
    assert record.regimes[INSTRUMENT] == "trending"
    assert record.candidates[0]["strategy_name"] == "momentum_v1"
    assert record.rejected[0]["reason"] == "not eligible"
    assert record.jev_reviews[0]["decision"] == CONFIRM
    assert record.jev_reviews[0]["tokens_used"] == 50
    assert record.allocations[0]["quantity"] == 10


async def test_a_quiet_tick_with_nothing_scanned_is_still_recorded() -> None:
    outcome = BarOutcome(
        ts=T0,
        exits=(),
        scan=ScanResult(candidates=(), rejected=()),
        jev=JevFilterResult(candidates=(), reviews=(), rejected=()),
        allocations=(),
    )
    store = _FakeStore()
    log = OpportunityAuditLog(store, IdGenerator())

    await log.record(outcome)

    record = store.inserted[0]
    assert record.instrument_ids == []
    assert record.candidates == []
    assert record.ts == T0


async def test_a_candidate_with_no_regime_is_recorded_as_an_empty_string() -> None:
    candidate = OpportunityCandidate(
        strategy_name="momentum_v1",
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M5,
        signal=make_signal(instrument_id=INSTRUMENT, price="100"),
        entry=Money.of("100"),
        stop=Money.of("98"),
        target=Money.of("104"),
        regime=None,
        generated_at=T0,
    )
    outcome = BarOutcome(
        ts=T0,
        exits=(),
        scan=ScanResult(candidates=(candidate,), rejected=()),
        jev=JevFilterResult(candidates=(candidate,), reviews=(), rejected=()),
        allocations=(),
    )
    store = _FakeStore()
    log = OpportunityAuditLog(store, IdGenerator())

    await log.record(outcome)

    assert store.inserted[0].regimes[INSTRUMENT] == ""
