"""EM-185: the matcher pairs signals one to one and never drops an unmatched one."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from emporos.parity.matcher import SignalMatcher, WithinTolerance
from emporos.parity.models import SignalMatch, SignalOrigin, SignalPoint
from tests.support.strategies import INSTRUMENT, T0

BAR = timedelta(minutes=5)


def point(
    origin: SignalOrigin,
    sequence: int = 1,
    ts: datetime = T0,
    side: OrderSide = OrderSide.BUY,
    kind: SignalKind = SignalKind.ENTRY,
    instrument_id: str = INSTRUMENT,
) -> SignalPoint:
    return SignalPoint(
        origin, f"{origin.value}-{sequence}", sequence, instrument_id, kind, side, ts,
        Money.of("100"), 10,
    )  # fmt: skip


def paper(**kw: object) -> SignalPoint:
    return point(SignalOrigin.PAPER, **kw)  # type: ignore[arg-type]


def backtest(**kw: object) -> SignalPoint:
    return point(SignalOrigin.BACKTEST, **kw)  # type: ignore[arg-type]


def test_identical_signals_match() -> None:
    p, b = paper(), backtest()
    assert SignalMatcher().match([p], [b]) == (SignalMatch(p, b),)


def test_a_signal_one_bar_off_matches_only_within_tolerance() -> None:
    p, b = paper(), backtest(ts=T0 + BAR)
    assert SignalMatcher().match([p], [b]) == (SignalMatch(p, None), SignalMatch(None, b))
    assert SignalMatcher(WithinTolerance(BAR)).match([p], [b]) == (SignalMatch(p, b),)
    assert (
        SignalMatcher(WithinTolerance(BAR - timedelta(seconds=1))).match([p], [b])[0].backtest
        is None
    )


def test_signals_of_a_different_side_kind_or_instrument_never_match() -> None:
    p = paper()
    for other in (
        backtest(side=OrderSide.SELL),
        backtest(kind=SignalKind.EXIT),
        backtest(instrument_id="NSE:9"),
    ):
        results = SignalMatcher(WithinTolerance(BAR)).match([p], [other])
        assert [m.matched for m in results] == [False, False]


def test_matching_is_one_to_one_and_duplicates_pair_in_order() -> None:
    p1, p2 = paper(sequence=1), paper(sequence=2)
    b1 = backtest(sequence=1)
    results = SignalMatcher().match([p1, p2], [b1])
    assert results == (SignalMatch(p1, b1), SignalMatch(p2, None))


def test_the_nearest_candidate_wins_then_the_earlier_sequence() -> None:
    p = paper(ts=T0 + BAR)
    near, far = backtest(sequence=2, ts=T0 + BAR), backtest(sequence=1, ts=T0)
    (first, *_) = SignalMatcher(WithinTolerance(BAR)).match([p], [far, near])
    assert first.backtest is near
    tie_a, tie_b = backtest(sequence=1, ts=T0), backtest(sequence=2, ts=T0)
    (chosen, *_) = SignalMatcher().match([paper()], [tie_b, tie_a])
    assert chosen.backtest is tie_a


def test_unmatched_signals_from_either_side_are_returned_not_dropped() -> None:
    p = paper(instrument_id="NSE:1")
    b = backtest(instrument_id="NSE:2", ts=T0 + timedelta(hours=1))
    results = SignalMatcher().match([p], [b])
    assert results == (SignalMatch(p, None), SignalMatch(None, b))


def test_a_match_needs_a_side_and_a_tolerance_is_never_negative() -> None:
    with pytest.raises(ValueError):
        SignalMatch(None, None)
    with pytest.raises(ValueError):
        WithinTolerance(timedelta(seconds=-1))
