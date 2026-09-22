"""relative_strength_v1 (EM-125) — rank the strategy's own universe by intraday momentum and trade
the extremes, rotating positions instead of holding a single per-instrument rule.

Design note (the cross-sectional seam, EM-125's own acceptance item): every other built-in strategy
decides using only the instrument its current bar belongs to. Ranking needs the OPPOSITE: on each
rebalance, a verdict about instrument A that depends on instrument B's price too. No new context
method was built for this -- `ctx.history.bars(instrument_id, timeframe, limit)` (`history.py`) was
already general over ANY instrument, and `ResolvedStrategyConfig.universe` (`config.py`) already
lets one strategy own many instruments (`momentum_v1` proved this by keeping one indicator track per
instrument since EM-69). So a cross-sectional read is just calling `ctx.history.bars` once per
instrument in `config.instrument_ids`, from the SAME strategy instance the runner already built.
Nothing in `StrategyContext`, `BarHistory` or the runner changed.

Cross-sectional signals need one extra timing rule: a rebalance waits until every configured
instrument has supplied its closed bar for the interval. `ctx.history.bars` never reveals an open
or future bar, and the live candle aggregator produces a flat, zero-volume bar for a silent
instrument, so this is a bounded same-close barrier rather than an unbounded wait for a tick. The
barrier prevents the first arriving symbol from being ranked against stale siblings and makes the
rank deterministic across the merge-sorted backtest feed and independently-derived paper/live
bars. `_last_rebalanced_at` then ensures exactly one rebalance per interval.

The rule: every `rebalance_every_bars` closed bars of the configured timeframe, score every
instrument in the universe by its close-to-close rate of change over `lookback` bars, drop anything
under `min_avg_volume` (illiquid names are not chased for a scalp) or inside `min_abs_roc_bps` of
flat (no real separation to trade), and rank what is left. Long the top `top_n`; short the bottom
`bottom_n` if `allow_short`. An instrument already held that falls out of its band is exited; one
already held that stays is left alone (no pyramiding, no needless churn). Concentration is bounded
twice over: `top_n + bottom_n` names are the intended book, and `risk.max_open_positions` (Phase 11)
is the hard cap this strategy sizes itself under, same as every other candidate here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, NamedTuple

from pydantic import model_validator

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.strategies.config import (
    ExactDecimal,
    NonNegativeInt,
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy

_BPS = Decimal(10_000)


class RelativeStrengthParameters(StrategyParameters):
    lookback: PositiveInt = 12  # bars of close-to-close return the score is measured over
    rebalance_every_bars: PositiveInt = 6  # how often (in this instrument's own bars) to re-rank
    top_n: PositiveInt = 2  # strongest names to hold long
    bottom_n: NonNegativeInt = 1  # weakest names to hold short (0 disables the short side)
    allow_short: bool = True
    min_avg_volume: PositiveInt = 50_000  # average bar volume over `lookback`; below this, excluded
    min_abs_roc_bps: ExactDecimal = Decimal(30)  # a name must move at least this much to qualify

    @model_validator(mode="after")
    def _consistent(self) -> RelativeStrengthParameters:
        if self.lookback < 2:
            raise ValueError("lookback must be at least 2 bars (one return to measure)")
        if self.bottom_n > 0 and not self.allow_short:
            raise ValueError("bottom_n > 0 needs allow_short=True")
        if self.min_abs_roc_bps < 0:
            raise ValueError("min_abs_roc_bps cannot be negative")
        return self


class _Score(NamedTuple):
    roc_bps: Decimal
    bar: Candle


@dataclass
class _Track(DayTrack):
    pass  # only the day/bar-of-day bookkeeping every intraday strategy keeps is needed here


class RelativeStrengthV1(IntradayStrategy):
    name: ClassVar[str] = "relative_strength_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = RelativeStrengthParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, RelativeStrengthParameters):
            raise TypeError("relative_strength_v1 needs RelativeStrengthParameters")
        self._p: RelativeStrengthParameters = config.parameters
        self._last_rebalanced_at: datetime | None = None
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track()

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        track.start_bar(bar)
        if not live or track.bar_of_day % self._p.rebalance_every_bars != 0:
            return
        if self._last_rebalanced_at is not None and bar.closes_at <= self._last_rebalanced_at:
            return  # another instrument's bar already triggered this interval's rebalance
        if not self._universe_is_current_through(bar.closes_at):
            return
        self._last_rebalanced_at = bar.closes_at
        self._rebalance()

    # --- the cross-sectional read: one pass over the whole configured universe -------------------
    def _rebalance(self) -> None:
        ctx = self._require_ctx()
        scores: dict[str, _Score] = {}
        for instrument_id in self._config.instrument_ids:
            score = self._score(instrument_id)
            if score is not None:
                scores[instrument_id] = score

        ranked = sorted(scores.items(), key=lambda kv: kv[1].roc_bps, reverse=True)
        longs = {
            iid for iid, s in ranked[: self._p.top_n] if s.roc_bps >= self._p.min_abs_roc_bps
        }
        shorts: set[str] = set()
        if self._p.allow_short and self._p.bottom_n > 0:
            shorts = {
                iid
                for iid, s in ranked[-self._p.bottom_n :]
                if s.roc_bps <= -self._p.min_abs_roc_bps and iid not in longs
            }

        held = {p.instrument_id: p for p in ctx.positions.open_positions()}
        still_held = self._exit_dropouts(held, longs, shorts)
        capacity = max(0, self._config.risk.max_open_positions - len(still_held))
        capacity = self._enter_band(ranked, longs, still_held, capacity, OrderSide.BUY, "top")
        self._enter_band(ranked, shorts, still_held, capacity, OrderSide.SELL, "bottom")

    def _score(self, instrument_id: str) -> _Score | None:
        bars = self._require_ctx().history.bars(
            instrument_id, self._config.timeframe, self._p.lookback + 1
        )
        if len(bars) < self._p.lookback + 1:
            return None  # not enough history yet to measure a return over `lookback` bars
        first, last = bars[0], bars[-1]
        if first.close.amount == ar.ZERO:
            return None
        avg_volume = ar.div(Decimal(sum(b.volume for b in bars)), Decimal(len(bars)))
        if avg_volume < Decimal(self._p.min_avg_volume):
            return None
        change = ar.div(ar.sub(last.close.amount, first.close.amount), first.close.amount)
        return _Score(ar.mul(change, _BPS), last)

    def _universe_is_current_through(self, closes_at: datetime) -> bool:
        """True once each member has a closed bar for this cross-sectional decision time."""
        for instrument_id in self._config.instrument_ids:
            latest = self._latest_bar(instrument_id)
            if latest is None or latest.closes_at < closes_at:
                return False
        return True

    def _exit_dropouts(
        self, held: dict[str, Position], longs: set[str], shorts: set[str]
    ) -> set[str]:
        still_held = set(held)
        for instrument_id, position in held.items():
            wants_out = (
                position.is_long
                and instrument_id not in longs
                or not position.is_long
                and instrument_id not in shorts
            )
            if not wants_out:
                continue
            bar = self._latest_bar(instrument_id)
            if bar is None:
                continue
            band = "top" if position.is_long else "bottom"
            self._exit(bar, position, f"dropped out of the {band}-{self._band_size(band)}")
            still_held.discard(instrument_id)
        return still_held

    def _enter_band(
        self,
        ranked: list[tuple[str, _Score]],
        band: set[str],
        still_held: set[str],
        capacity: int,
        side: OrderSide,
        label: str,
    ) -> int:
        for rank, (instrument_id, score) in enumerate(ranked, start=1):
            if capacity <= 0:
                break
            if instrument_id not in band or instrument_id in still_held:
                continue
            if not self._entries_open(score.bar):
                continue
            why = (
                f"ranked #{rank} of {len(ranked)} by {self._p.lookback}-bar RoC "
                f"{self._show(score.roc_bps)} bps ({label}-{self._band_size(label)})"
            )
            if self._enter(score.bar, side, why):
                still_held.add(instrument_id)
                capacity -= 1
        return capacity

    def _band_size(self, label: str) -> int:
        return self._p.top_n if label == "top" else self._p.bottom_n

    def _latest_bar(self, instrument_id: str) -> Candle | None:
        bars = self._require_ctx().history.bars(instrument_id, self._config.timeframe, 1)
        return bars[-1] if bars else None

    @staticmethod
    def _show(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01"), context=ar.CONTEXT), "f")
