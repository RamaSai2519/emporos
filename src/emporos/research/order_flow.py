"""Order-flow and liquidity-conditioned features (EM-181), computed entirely from OHLCV candles.

This codebase ingests and stores only OHLCV bars — never a historical bid/ask or order-book
series. Angel One's WebSocket feed DOES carry a best bid/ask on its QUOTE frames
(`emporos.broker.models.Quote.bid`/`.ask`, populated from `DepthLevel` in
`emporos.broker.angelone.mapping`) and full market depth exists as a protocol concept
(`emporos.broker.angelone.models.QuoteDepth`), but DEPTH-mode subscription is explicitly out of
v1 scope (`emporos.broker.angelone.ws_market`'s `SubscriptionMode` comment: "DEPTH (4) is
deliberately absent: it is NSE-only, 50 tokens per..."), and even the best-bid/ask QUOTE stream is
never persisted as a time series — it is read live and discarded. So every feature below is a
PROXY built from closing prices and volume, not a measurement of actual order-book imbalance or
quoted spread; each docstring says exactly what real, tick-level information it is standing in
for and where that substitution can mislead. A future subtask that starts persisting live quotes
could add a real spread/imbalance feature without changing anything downstream of `Feature` —
`FeatureRegistry`/`AlphaDiscoveryEngine` do not care where a `Decimal` came from.
"""

from __future__ import annotations

from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.research.features import CausalHistory
from emporos.research.regimes import amihud_illiquidity
from emporos.research.sessions import split_sessions

_ZERO = Decimal(0)


class OrderFlowImbalance:
    """A close-location-value proxy for signed volume: `((close-low)-(high-close))/(high-low)`,
    ranging -1 (closed at the low) to +1 (closed at the high), scaled by the bar's volume. This is
    the "money flow volume" component of the classic Accumulation/Distribution line, repurposed
    here as a standalone feature.

    LIMITATION: a real order-imbalance measure classifies every individual trade as buyer- or
    seller-initiated (the tick rule, or Lee-Ready against the prevailing quote) and sums signed
    volume directly. This proxy instead assumes the bar's CLOSING position within its own
    high-low range approximates where most of the bar's volume traded — which is wrong whenever a
    bar's volume is dominated by one early print followed by a drift to a very different close
    (a single large block trade at the open, say, in an otherwise quiet bar), or when high and low
    are set by brief wicks that carried little volume. It is a proxy for the NET DIRECTION volume
    leaned toward, not a count of buy versus sell volume.
    """

    def compute(self, history: CausalHistory) -> Decimal | None:
        bar = history.last
        high, low, close = bar.high.amount, bar.low.amount, bar.close.amount
        span = high - low
        if span == _ZERO:
            return None
        location = DecimalMath.divide((close - low) - (high - close), span)
        return location * bar.volume


class RelativeVolumeTimeOfDay:
    """Today's volume at this bar-of-day slot, relative to the average volume THIS SAME slot saw
    on the trailing `window` prior sessions — normalizing away the ordinary U-shaped intraday
    volume curve (heavy at the open and close, thin midday) that a raw trailing volume average
    would otherwise mistake for a signal. Positive means unusually busy for this time of day.

    LIMITATION: needs `window` prior FULL sessions with a bar at the same slot to mean anything;
    early in a series (a new listing, or near the start of the cached history) it is `None`.
    """

    def __init__(self, window: int = 10) -> None:
        if window < 1:
            raise ValueError("a window needs at least one prior session")
        self._window = window

    def compute(self, history: CausalHistory) -> Decimal | None:
        sessions = split_sessions(list(history))
        if len(sessions) < 2:
            return None
        _, today = sessions[-1]
        bar_of_day = len(today) - 1
        prior_sessions = sessions[max(0, len(sessions) - 1 - self._window) : -1]
        same_slot_volumes = [
            Decimal(bars[bar_of_day].volume) for _, bars in prior_sessions if len(bars) > bar_of_day
        ]
        if len(same_slot_volumes) < 2:
            return None
        average = DecimalMath.mean(same_slot_volumes)
        if average == _ZERO:
            return None
        return DecimalMath.divide(Decimal(today[bar_of_day].volume) - average, average)


class TradeIntensity:
    """Current volume relative to the trailing `window`-bar average — deliberately NOT
    time-of-day normalized (unlike `RelativeVolumeTimeOfDay`): a raw "is more happening right now
    than usual" reading, useful precisely because it reacts within a session (a sudden burst 20
    minutes after the open), where the time-of-day-normalized version by design would not.

    LIMITATION: without time-of-day normalization this is naturally elevated near every session's
    open and close; segment by `emporos.research.sessions` position or by
    `emporos.research.regimes.MarketRegimeAxis` if that matters for a given study.
    """

    def __init__(self, window: int = 20) -> None:
        if window < 2:
            raise ValueError("a window needs at least two bars")
        self._window = window

    def compute(self, history: CausalHistory) -> Decimal | None:
        if len(history) <= self._window:
            return None
        trailing = [Decimal(c.volume) for c in history[-self._window - 1 : -1]]
        average = DecimalMath.mean(trailing)
        if average == _ZERO:
            return None
        return DecimalMath.divide(Decimal(history.last.volume) - average, average)


class SpreadProxyFeature:
    """The Amihud (2002) illiquidity ratio (`emporos.research.regimes.amihud_illiquidity`) as a
    continuous feature rather than a discrete `SpreadProxyBucket` label — needed here because
    `AlphaDiscoveryEngine`'s rank-IC and decile-spread statistics need a Decimal-valued feature to
    rank, not a three-bucket classification. Same proxy, same limitation: it is a substitute for a
    quoted spread this platform never persists, not a measurement of one.

    Deliberately stateless — the previous close comes from `history` itself, never a remembered
    attribute, since `AlphaDiscoveryEngine`/`FeatureStudy` reuse ONE `Feature` instance across
    every instrument in a study; a stateful "previous close" would leak one instrument's last bar
    into the next instrument's first (the same leak `RegimeAxisFactory` exists to prevent for
    regime axes)."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        bar = history.last
        previous_close = history[-2].close.amount if len(history) >= 2 else None
        return amihud_illiquidity(previous_close, bar.close.amount, bar.volume)
