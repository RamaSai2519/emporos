"""EM-45: the exponential-backoff schedule with jitter."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporos.broker.backoff import BackoffPolicy, RandomJitter
from emporos.core.errors import ConfigurationError
from tests.support.fakes import FixedJitter

POLICY = BackoffPolicy(base_delay=1.0, max_delay=30.0, max_attempts=8)


def test_the_ceiling_doubles_then_saturates_at_the_maximum() -> None:
    assert [POLICY.ceiling(n) for n in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_jitter_spans_the_upper_half_of_the_ceiling() -> None:
    assert POLICY.delay(3, FixedJitter(0.0)) == 4.0  # ceiling 8 -> floor of 4
    assert POLICY.delay(3, FixedJitter(0.999999)) == pytest.approx(8.0, abs=1e-5)


def test_the_delay_never_collapses_to_zero_however_lucky_the_draw() -> None:
    assert all(POLICY.delay(n, FixedJitter(0.0)) >= POLICY.ceiling(n) / 2 for n in range(20))


@given(
    retry=st.integers(min_value=0, max_value=200), draw=st.floats(min_value=0, max_value=0.999999)
)
def test_every_delay_is_within_its_bounds(retry: int, draw: float) -> None:
    delay = POLICY.delay(retry, FixedJitter(draw))
    assert POLICY.ceiling(retry) / 2 <= delay <= POLICY.ceiling(retry) <= POLICY.max_delay


def test_random_jitter_is_in_range_and_reproducible_from_a_seed() -> None:
    first, second = RandomJitter(seed=7), RandomJitter(seed=7)
    draws = [first.fraction() for _ in range(50)]
    assert draws == [second.fraction() for _ in range(50)]
    assert all(0.0 <= draw < 1.0 for draw in draws)
    assert len(set(draws)) > 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_delay": 0, "max_delay": 1, "max_attempts": 1},
        {"base_delay": 2, "max_delay": 1, "max_attempts": 1},
        {"base_delay": 1, "max_delay": 2, "max_attempts": 0},
        {"base_delay": 1, "max_delay": 2, "max_attempts": 1, "multiplier": 0.5},
    ],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ConfigurationError):
        BackoffPolicy(**kwargs)  # type: ignore[arg-type]
