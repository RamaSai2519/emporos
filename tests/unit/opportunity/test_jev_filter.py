from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.jev.config import JevConfig
from emporos.jev.models import (
    ABSTAIN,
    CONFIRM,
    CONFIRMATION,
    RANKING,
    REJECT,
    STRATEGY_SELECTION,
    JevDecision,
    JevRequest,
)
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.opportunity.models import OpportunityCandidate
from emporos.strategies.regime import MarketRegime
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, make_signal

DECIDED_AT = datetime(2026, 1, 5, 3, 46, tzinfo=UTC)


def _candidate(
    instrument_id: str = INSTRUMENT,
    strategy_name: str = "momentum_v1",
    confidence: Decimal = Decimal(1),
) -> OpportunityCandidate:
    return OpportunityCandidate(
        strategy_name=strategy_name,
        instrument_id=instrument_id,
        timeframe=Timeframe.M5,
        signal=make_signal(instrument_id=instrument_id, price="100"),
        entry=Money.of("100"),
        stop=Money.of("98"),
        target=Money.of("104"),
        regime=MarketRegime.TRENDING,
        generated_at=T0,
        confidence=confidence,
    )


class _ScriptedProvider:
    """A `JevProvider` double that returns queued decisions in order, or a fixed one for every
    call — whichever the test needs. Records every request it was asked to decide."""

    def __init__(self, *decisions: JevDecision) -> None:
        self._decisions = list(decisions)
        self.requests: list[JevRequest] = []

    async def decide(self, request: JevRequest) -> JevDecision:
        self.requests.append(request)
        return self._decisions.pop(0) if self._decisions else self._decisions_exhausted()

    @staticmethod
    def _decisions_exhausted() -> JevDecision:
        raise AssertionError("scripted Jev provider ran out of decisions")


def _decision(
    decision: str = CONFIRM, confidence: Decimal | None = Decimal("0.9"), error: str | None = None
) -> JevDecision:
    return JevDecision(
        decision=decision,
        confidence=confidence,
        provider="test",
        model="test-model",
        config_version=None,
        requested_at=DECIDED_AT,
        latency_ms=5,
        error=error,
    )


async def test_disabled_jev_passes_every_candidate_through_untouched() -> None:
    candidates = (_candidate(),)
    filter_ = JevMetaDecisionFilter(_ScriptedProvider(), JevConfig(enabled=False))

    result = await filter_.apply(candidates)

    assert result.candidates == candidates
    assert result.reviews == ()
    assert result.rejected == ()


async def test_confirmation_mode_keeps_a_confirmed_candidate() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    candidate = _candidate()

    result = await filter_.apply((candidate,))

    assert result.candidates == (candidate,)
    assert len(result.reviews) == 1
    assert result.rejected == ()


async def test_confirmation_mode_drops_a_rejected_candidate() -> None:
    provider = _ScriptedProvider(_decision(REJECT, confidence=None))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    candidate = _candidate()

    result = await filter_.apply((candidate,))

    assert result.candidates == ()
    assert len(result.rejected) == 1
    assert result.rejected[0].candidate is candidate


async def test_confirmation_below_threshold_is_dropped() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM, confidence=Decimal("0.3")))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, confidence_threshold=0.6)
    )

    result = await filter_.apply((_candidate(),))

    assert result.candidates == ()


async def test_confirmation_with_no_confidence_is_not_threshold_checked() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM, confidence=None))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, confidence_threshold=0.9)
    )

    result = await filter_.apply((_candidate(),))

    assert len(result.candidates) == 1


async def test_ranking_with_no_jev_confidence_keeps_the_original_confidence() -> None:
    candidate = _candidate(confidence=Decimal("0.7"))
    provider = _ScriptedProvider(_decision(CONFIRM, confidence=None))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=RANKING))

    result = await filter_.apply((candidate,))

    assert result.candidates[0].confidence == Decimal("0.7")


async def test_confirmation_at_or_above_threshold_survives() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM, confidence=Decimal("0.6")))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, confidence_threshold=0.6)
    )

    result = await filter_.apply((_candidate(),))

    assert len(result.candidates) == 1


async def test_a_failed_request_is_rejected_when_fail_closed() -> None:
    provider = _ScriptedProvider(_decision(ABSTAIN, confidence=None, error="timeout"))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, fail_open=False)
    )

    result = await filter_.apply((_candidate(),))

    assert result.candidates == ()
    assert len(result.rejected) == 1


async def test_a_failed_request_passes_when_fail_open() -> None:
    provider = _ScriptedProvider(_decision(ABSTAIN, confidence=None, error="timeout"))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, fail_open=True)
    )

    result = await filter_.apply((_candidate(),))

    assert len(result.candidates) == 1


async def test_an_abstain_without_error_follows_fail_open_too() -> None:
    provider = _ScriptedProvider(_decision(ABSTAIN, confidence=None))
    filter_ = JevMetaDecisionFilter(
        provider, JevConfig(enabled=True, mode=CONFIRMATION, fail_open=False)
    )

    result = await filter_.apply((_candidate(),))

    assert result.candidates == ()


async def test_ranking_mode_scales_confidence_and_resorts() -> None:
    weak = _candidate(instrument_id=INSTRUMENT, confidence=Decimal(1))
    strong = _candidate(instrument_id=OTHER_INSTRUMENT, confidence=Decimal(1))
    # Both candidates have identical edge, so Jev confidence alone decides the resort. Both stay
    # above the default confidence_threshold so neither is dropped, only reordered.
    provider = _ScriptedProvider(
        _decision(CONFIRM, Decimal("0.7")), _decision(CONFIRM, Decimal("0.9"))
    )
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=RANKING))

    result = await filter_.apply((weak, strong))

    assert len(result.candidates) == 2
    assert result.candidates[0].strategy_name == "momentum_v1"
    assert result.candidates[0].instrument_id == OTHER_INSTRUMENT  # the 0.9-confidence one is first
    assert result.candidates[0].confidence == Decimal("0.9")


async def test_ranking_mode_drops_candidates_jev_rejects() -> None:
    provider = _ScriptedProvider(_decision(REJECT, confidence=None))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=RANKING))

    result = await filter_.apply((_candidate(),))

    assert result.candidates == ()
    assert len(result.rejected) == 1


async def test_strategy_selection_mode_also_reweights_and_resorts() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM, Decimal("0.7")))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=STRATEGY_SELECTION))

    result = await filter_.apply((_candidate(),))

    assert len(result.candidates) == 1
    assert result.candidates[0].confidence == Decimal("0.7")


async def test_the_request_sent_to_jev_carries_the_candidates_context() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
    candidate = _candidate()

    await filter_.apply((candidate,))

    request = provider.requests[0]
    assert request.symbol == candidate.instrument_id
    assert request.timeframe == "5m"
    assert request.regime == "trending"
    assert request.strategy_name == "momentum_v1"
    assert request.entry == Decimal("100")


async def test_a_candidate_with_no_regime_sends_none() -> None:
    provider = _ScriptedProvider(_decision(CONFIRM))
    filter_ = JevMetaDecisionFilter(provider, JevConfig(enabled=True, mode=CONFIRMATION))
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

    await filter_.apply((candidate,))

    assert provider.requests[0].regime is None
