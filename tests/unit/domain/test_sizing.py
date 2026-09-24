"""EM-191 F4: the declared position value, and how a stated size becomes the one used."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize, SizeResolver, SizeSource

D = Decimal


def test_quantity_rounds_down_and_is_zero_when_one_share_overshoots() -> None:
    size = DeclaredSize(D(25_000))

    assert size.quantity_at(Money.of(1000)) == 25
    assert size.quantity_at(Money.of(1001)) == 24
    assert size.quantity_at(Money.of(30_000)) == 0
    assert size.quantity_at(Money.of(0)) == 0


def test_a_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        DeclaredSize(D(0))
    with pytest.raises(ValueError, match="positive"):
        DeclaredSize(D(-1))


def test_a_size_above_a_limit_says_so() -> None:
    assert DeclaredSize(D(100_000)).exceeds(D(25_000))
    assert not DeclaredSize(D(25_000)).exceeds(D(25_000))


def test_with_nothing_stated_the_risk_limit_is_the_size() -> None:
    size = SizeResolver(D(25_000)).resolve()

    assert size == DeclaredSize(D(25_000), SizeSource.RISK_DEFAULT)


def test_a_declaration_or_an_override_states_the_size() -> None:
    resolver = SizeResolver(D(25_000))

    assert resolver.resolve(declared=D(50_000)) == DeclaredSize(D(50_000))
    assert resolver.resolve(override=D(5_000)) == DeclaredSize(D(5_000))
    assert resolver.resolve(declared=D(5_000), override=D(5_000)).source is SizeSource.DECLARED


def test_an_override_cannot_contradict_the_declaration() -> None:
    with pytest.raises(ValueError, match="declaration fixes"):
        SizeResolver(D(25_000)).resolve(declared=D(25_000), override=D(50_000))
