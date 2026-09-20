"""Exact-as-possible arithmetic for the metrics (plan.md §10, "no floats near money").

Ratios such as Sharpe need square roots and fractional powers, which `Decimal` provides at a
chosen precision. Every function here runs under ONE fixed context (34 significant digits, half
even), whatever context the caller happens to have, so a metric is the same number on every
machine and in every thread. Results are rounded for display only when a report is rendered.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)
ONE = Decimal(1)


class DecimalMath:
    @staticmethod
    def mean(values: Sequence[Decimal]) -> Decimal:
        if not values:
            raise ValueError("the mean of nothing is undefined")
        with localcontext(CONTEXT):
            return sum(values, ZERO) / len(values)

    @staticmethod
    def sample_variance(values: Sequence[Decimal]) -> Decimal:
        """Variance with the n-1 (sample) denominator."""
        if len(values) < 2:
            raise ValueError("a sample variance needs at least two values")
        with localcontext(CONTEXT):
            centre = sum(values, ZERO) / len(values)
            return sum(((v - centre) ** 2 for v in values), ZERO) / (len(values) - 1)

    @staticmethod
    def sample_stdev(values: Sequence[Decimal]) -> Decimal:
        """Standard deviation with the n-1 (sample) denominator."""
        if len(values) < 2:
            raise ValueError("a sample standard deviation needs at least two values")
        with localcontext(CONTEXT):
            return DecimalMath.sample_variance(values).sqrt()

    @staticmethod
    def sqrt(value: Decimal) -> Decimal:
        with localcontext(CONTEXT):
            return value.sqrt()

    @staticmethod
    def divide(numerator: Decimal, denominator: Decimal) -> Decimal:
        with localcontext(CONTEXT):
            return numerator / denominator

    @staticmethod
    def power(base: Decimal, exponent: Decimal) -> Decimal:
        """`base ** exponent` for a positive base and any exponent."""
        if base <= ZERO:
            raise ValueError("a fractional power needs a positive base")
        with localcontext(CONTEXT):
            return base**exponent
