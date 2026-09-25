"""The as-of context for an event at a decision time (PROFIT_PLAN §12.2, EM-239, L-D3).

`AsOfContextBuilder.context(event, decision_at)` returns the numbers a decision may use, computed
only from 5-minute bars that had CLOSED by `decision_at` (`IntradayBars` cannot reach a later one)
and from sessions before it. A number that cannot be known at that instant is left out, never
filled in; a name with no instrument id gets the market lines only.

Keys (percentages are in percent; every price is on the raw series):
* `session`: `open` (a bar of the decision day has closed, the session is not over), `after_close`
  (the day's 15:25 bar has closed) or `closed` (no bar of the decision day yet: before the first
  bar closes, a weekend or a holiday); `last_session`: the ISO date the numbers describe.
* `last_price`: the close of the last completed bar. `prev_close`: the close of the session before
  `last_session`. `move_since_prev_close_pct`: last over previous close (not in `closed`).
* `move_since_event_pct`: last price over the price at the event (the close of the last bar
  completed at or before the event's usable time).
* `vwap_distance_pct`: last price over the session's VWAP so far (typical price x volume).
* `volume_vs_norm`: the session's cumulative volume so far over the median cumulative volume
  through the same bar of the last 20 sessions (needs 5).
* `ret_5d_pct`, `ret_20d_pct`: last price over the close 5 / 20 sessions before `last_session`.
  `vol_20d_pct`: standard deviation of the daily close-to-close returns of those 20 sessions.
* `nifty_move_since_prev_close_pct`, `nifty_ret_5d_pct`; `sector_index` (its name) and
  `sector_move_since_prev_close_pct`; `india_vix` and `india_vix_change_pct` since its previous
  close.
* F&O open interest: not present. The archive holds index contracts only (no stock-derivative
  rows), so the keys are omitted rather than guessed."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import pairwise
from math import isfinite, sqrt
from statistics import median

from emporos.core.clock import IST
from emporos.eventtrader.events import MarketContext, MarketEvent
from emporos.research.market_context.bars import BarSeriesCache, IntradayBars, SessionBars

__all__ = ["CONTEXT_KEYS", "AsOfContextBuilder", "SectorMap"]

SESSION_END = time(15, 30)
NORM_SESSIONS = 20
MIN_NORM_SESSIONS = 5
MIN_VOL_SESSIONS = 10

CONTEXT_KEYS = (
    "session", "last_session", "last_price", "prev_close", "move_since_prev_close_pct",
    "move_since_event_pct", "vwap_distance_pct", "volume_vs_norm", "ret_5d_pct", "ret_20d_pct",
    "vol_20d_pct", "nifty_move_since_prev_close_pct", "nifty_ret_5d_pct", "sector_index",
    "sector_move_since_prev_close_pct", "india_vix", "india_vix_change_pct",
)  # fmt: skip


@dataclass(frozen=True)
class SectorMap:
    """A trading symbol's sector index: (series id, display name), or None."""

    by_symbol: Mapping[str, tuple[str, str]]

    def of(self, symbol: str) -> tuple[str, str] | None:
        return self.by_symbol.get(symbol)


@dataclass(frozen=True)
class _View:
    """One series as of the decision time."""

    state: str  # open | after_close | closed
    anchor: date
    last: float
    prev_close: float | None
    today: SessionBars | None
    prior: list[SessionBars]

    @property
    def move_pct(self) -> float | None:
        if self.state == "closed" or self.prev_close in (None, 0):
            return None
        assert self.prev_close is not None
        return (self.last / self.prev_close - 1) * 100

    def ret_pct(self, sessions: int) -> float | None:
        if len(self.prior) < sessions:
            return None
        ref = self.prior[-sessions].closes[-1]
        return (self.last / ref - 1) * 100 if ref else None


def _view(bars: IntradayBars, decision_at: datetime) -> _View | None:
    day = decision_at.astimezone(IST).date()
    anchor = bars.last_session_day_on_or_before(day, decision_at)
    last = bars.price_at(decision_at)
    if anchor is None or last is None:
        return None
    today = bars.session(anchor, decision_at)
    assert today is not None
    finished = today.ends[-1].astimezone(IST).time() >= SESSION_END
    state = "closed" if anchor != day else ("after_close" if finished else "open")
    prior = bars.sessions_before(anchor, NORM_SESSIONS, decision_at)
    prev = prior[-1].closes[-1] if prior else None
    return _View(state, anchor, last, prev, today, prior)


def _put(lines: dict[str, str | float], key: str, value: float | None) -> None:
    if value is not None and isfinite(value):
        lines[key] = value


class AsOfContextBuilder:
    def __init__(
        self, series: BarSeriesCache, nifty_id: str, vix_id: str, sectors: SectorMap
    ) -> None:
        self._series = series
        self._nifty = nifty_id
        self._vix = vix_id
        self._sectors = sectors

    def context(self, event: MarketEvent, decision_at: datetime) -> MarketContext:
        lines: dict[str, str | float] = {}
        if event.instrument_id:
            self._name_lines(lines, event, decision_at)
        self._market_lines(lines, event.symbol, decision_at)
        return MarketContext(lines)

    # -- the name -------------------------------------------------------------------------------

    def _name_lines(
        self, lines: dict[str, str | float], event: MarketEvent, decision_at: datetime
    ) -> None:
        bars = self._series.bars(event.instrument_id)
        view = _view(bars, decision_at)
        if view is None:
            return
        lines["session"] = view.state
        lines["last_session"] = view.anchor.isoformat()
        lines["last_price"] = view.last
        _put(lines, "prev_close", view.prev_close)
        _put(lines, "move_since_prev_close_pct", view.move_pct)
        at_event = bars.price_at(event.usable_from)
        if at_event:
            _put(lines, "move_since_event_pct", (view.last / at_event - 1) * 100)
        if view.state != "closed" and view.today is not None:
            _put(lines, "vwap_distance_pct", self._vwap_distance(view))
            _put(lines, "volume_vs_norm", self._volume_vs_norm(view))
        _put(lines, "ret_5d_pct", view.ret_pct(5))
        _put(lines, "ret_20d_pct", view.ret_pct(20))
        _put(lines, "vol_20d_pct", self._volatility(view))

    @staticmethod
    def _vwap_distance(view: _View) -> float | None:
        assert view.today is not None
        today = view.today
        volume = sum(today.volumes)
        if volume <= 0:
            return None
        typical = sum(
            (h + low + c) / 3 * v
            for h, low, c, v in zip(
                today.highs, today.lows, today.closes, today.volumes, strict=True
            )
        )
        vwap = typical / volume
        return (view.last / vwap - 1) * 100 if vwap else None

    @staticmethod
    def _volume_vs_norm(view: _View) -> float | None:
        assert view.today is not None
        k = len(view.today)
        history = [sum(s.volumes[:k]) for s in view.prior if len(s) >= k]
        if len(history) < MIN_NORM_SESSIONS:
            return None
        norm = median(history)
        return sum(view.today.volumes) / norm if norm > 0 else None

    @staticmethod
    def _volatility(view: _View) -> float | None:
        closes = [s.closes[-1] for s in view.prior]
        if len(closes) < MIN_VOL_SESSIONS + 1:
            return None
        returns = [b / a - 1 for a, b in pairwise(closes) if a]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return sqrt(variance) * 100

    # -- the market -----------------------------------------------------------------------------

    def _market_lines(
        self, lines: dict[str, str | float], symbol: str, decision_at: datetime
    ) -> None:
        nifty = _view(self._series.bars(self._nifty), decision_at)
        if nifty is not None:
            _put(lines, "nifty_move_since_prev_close_pct", nifty.move_pct)
            _put(lines, "nifty_ret_5d_pct", nifty.ret_pct(5))
        sector = self._sectors.of(symbol)
        if sector is not None:
            view = _view(self._series.bars(sector[0]), decision_at)
            if view is not None:
                lines["sector_index"] = sector[1]
                _put(lines, "sector_move_since_prev_close_pct", view.move_pct)
        vix = _view(self._series.bars(self._vix), decision_at)
        if vix is not None:
            lines["india_vix"] = vix.last
            _put(lines, "india_vix_change_pct", vix.move_pct)
