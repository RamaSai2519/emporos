"""ATM implied volatility, 25-delta skew and the straddle's implied move for one slice (EM-244)."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from emporos.research.iv.black76 import Right, black76_delta, implied_vol
from emporos.research.iv.slices import ChainSlice, Quote

__all__ = ["SliceMetrics", "SliceMetricsCalculator"]

TARGET_DELTA = 0.25


@dataclass(frozen=True)
class SliceMetrics:
    atm_strike: float
    atm_iv: float | None  # mean of the ATM call's and put's implied vols (either, if only one)
    call25_iv: float | None
    put25_iv: float | None
    straddle: float | None  # ATM call + put settle
    implied_move: float | None  # straddle / forward

    @property
    def skew25(self) -> float | None:
        if self.put25_iv is None or self.call25_iv is None:
            return None
        return self.put25_iv - self.call25_iv


class SliceMetricsCalculator:
    def compute(self, chain: ChainSlice, rate: float) -> SliceMetrics | None:
        strikes = sorted({q.strike for q in chain.quotes})
        if not strikes or chain.years <= 0:
            return None
        atm = min(strikes, key=lambda k: abs(k - chain.forward))
        at_atm = {q.right: q for q in chain.quotes if q.strike == atm}
        ivs = [self._iv(q, chain, rate) for q in at_atm.values()]
        known = [v for v in ivs if v is not None]
        straddle = (
            at_atm[Right.CALL].settle + at_atm[Right.PUT].settle if len(at_atm) == 2 else None
        )
        return SliceMetrics(
            atm,
            sum(known) / len(known) if known else None,
            self._delta_iv(chain, rate, Right.CALL),
            self._delta_iv(chain, rate, Right.PUT),
            straddle,
            straddle / chain.forward if straddle is not None else None,
        )

    @staticmethod
    def _iv(quote: Quote, chain: ChainSlice, rate: float) -> float | None:
        return implied_vol(
            quote.settle, chain.forward, quote.strike, chain.years, rate, quote.right
        )

    def _delta_iv(self, chain: ChainSlice, rate: float, right: Right) -> float | None:
        """The IV at absolute delta 0.25 on the out-of-the-money side, interpolated linearly in
        delta between the two strikes that bracket it; None if the strikes do not bracket it."""
        points: list[tuple[float, float]] = []  # (absolute delta, iv), out of the money only
        for quote in chain.quotes:
            if quote.right is not right:
                continue
            if (right is Right.CALL and quote.strike < chain.forward) or (
                right is Right.PUT and quote.strike > chain.forward
            ):
                continue
            vol = self._iv(quote, chain, rate)
            if vol is None:
                continue
            delta = abs(black76_delta(chain.forward, quote.strike, chain.years, rate, vol, right))
            points.append((delta, vol))
        points.sort()
        for (d_low, v_low), (d_high, v_high) in pairwise(points):
            if d_low <= TARGET_DELTA <= d_high and d_high > d_low:
                weight = (TARGET_DELTA - d_low) / (d_high - d_low)
                return v_low + weight * (v_high - v_low)
        return None
