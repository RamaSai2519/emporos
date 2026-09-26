"""Black-76: European options on a forward or future, and the implied volatility that reproduces a
price (EM-244).

`price = e^(-rT) * [F N(d1) - K N(d2)]` for a call, `e^(-rT) * [K N(-d2) - F N(-d1)]` for a put,
`d1 = (ln(F/K) + sigma^2 T / 2) / (sigma sqrt(T))`. NSE index and stock options are European,
so Black-76 on the future with the RBI policy repo rate as the discount rate is the declared model.
The implied volatility is found by bisection on [1e-4, 5]: the
price is strictly increasing in volatility, so a price outside the no-arbitrage band has no root and
gives None (never a guess)."""

from __future__ import annotations

import math
from enum import StrEnum

__all__ = ["Right", "black76_delta", "black76_price", "implied_vol"]

_LOW_VOL, _HIGH_VOL = 1e-4, 5.0
_TOLERANCE = 1e-10


class Right(StrEnum):
    CALL = "CE"
    PUT = "PE"


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(forward: float, strike: float, years: float, vol: float) -> tuple[float, float]:
    spread = vol * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * spread * spread) / spread
    return d1, d1 - spread


def black76_price(
    forward: float, strike: float, years: float, rate: float, vol: float, right: Right
) -> float:
    discount = math.exp(-rate * years)
    if years <= 0 or vol <= 0:
        intrinsic = (
            max(forward - strike, 0.0) if right is Right.CALL else max(strike - forward, 0.0)
        )
        return discount * intrinsic
    d1, d2 = _d1_d2(forward, strike, years, vol)
    if right is Right.CALL:
        return discount * (forward * _cdf(d1) - strike * _cdf(d2))
    return discount * (strike * _cdf(-d2) - forward * _cdf(-d1))


def black76_delta(
    forward: float, strike: float, years: float, rate: float, vol: float, right: Right
) -> float:
    """Delta with respect to the FORWARD, discounted (a call's is in (0, 1), a put's in (-1, 0))."""
    d1, _ = _d1_d2(forward, strike, years, vol)
    discount = math.exp(-rate * years)
    return discount * _cdf(d1) if right is Right.CALL else -discount * _cdf(-d1)


def implied_vol(
    price: float, forward: float, strike: float, years: float, rate: float, right: Right
) -> float | None:
    """The volatility at which Black-76 gives `price`, or None if there is none (a price at or
    below the option's discounted intrinsic value, or above its upper bound)."""
    if price <= 0 or forward <= 0 or strike <= 0 or years <= 0:
        return None
    low_price = black76_price(forward, strike, years, rate, _LOW_VOL, right)
    high_price = black76_price(forward, strike, years, rate, _HIGH_VOL, right)
    if not low_price < price < high_price:
        return None
    low, high = _LOW_VOL, _HIGH_VOL
    for _ in range(200):
        mid = 0.5 * (low + high)
        gap = black76_price(forward, strike, years, rate, mid, right) - price
        if abs(gap) < _TOLERANCE:
            return mid
        if gap > 0:
            high = mid
        else:
            low = mid
    return 0.5 * (low + high)
