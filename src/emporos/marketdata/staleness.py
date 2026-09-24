"""Per-instrument feed staleness (EM-53, plan.md §7).

`now - last_tick` beyond a liquidity-aware threshold marks an instrument STALE. STALE **blocks new
entries but never exits**: being unable to leave a position because the data went quiet is more
dangerous than the stale data itself. Phase 11's `StaleDataGuard` risk rule consumes
`StalenessView` — `permits_entry` for entries, and exits are always permitted.

Staleness is *derived* from the injected clock whenever it is asked, so the risk engine never
sees an out-of-date answer regardless of how often `check()` runs; `check()` exists to notice
*transitions* and raise alerts. Rules:

* liveness is the wall-clock arrival of ANY tick (out-of-order ones included);
* it is only judged inside the session window, measured from the session open at the earliest,
  so overnight silence is not "stale" and a quiet open is;
* a dropped feed makes every watched instrument stale immediately (nothing can be fresh);
* a fresh tick recovers an instrument at once.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import IST, Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.ticks import Tick
from emporos.marketdata.session import SessionWindow

_LOG = logging.getLogger(__name__)


class LiquidityTier(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


DEFAULT_TIER_SECONDS = {
    LiquidityTier.HIGH: 10.0,
    LiquidityTier.MEDIUM: 30.0,
    LiquidityTier.LOW: 120.0,  # a thin stock legitimately goes quiet
}


class ThresholdPolicy(Protocol):
    def threshold(self, instrument_id: str) -> timedelta: ...


class TieredThresholds:
    """A quiet-time allowance per liquidity tier; unlisted instruments use `default_tier`."""

    def __init__(
        self,
        tiers: Mapping[str, LiquidityTier] | None = None,
        seconds_by_tier: Mapping[LiquidityTier, float] | None = None,
        default_tier: LiquidityTier = LiquidityTier.MEDIUM,
    ) -> None:
        self._tiers = dict(tiers or {})
        self._seconds = dict(seconds_by_tier or DEFAULT_TIER_SECONDS)
        self._default = default_tier
        if any(seconds <= 0 for seconds in self._seconds.values()):
            raise ConfigurationError("staleness thresholds must be positive")
        if not {*self._tiers.values(), default_tier} <= self._seconds.keys():
            raise ConfigurationError("every liquidity tier in use needs a threshold")

    def threshold(self, instrument_id: str) -> timedelta:
        tier = self._tiers.get(instrument_id, self._default)
        return timedelta(seconds=self._seconds[tier])


class StalenessView(Protocol):
    """What the risk engine may ask. Defined here, beside the data it describes."""

    def is_stale(self, instrument_id: str) -> bool: ...

    def permits_entry(self, instrument_id: str) -> bool: ...

    def permits_exit(self, instrument_id: str) -> bool: ...


class StalenessListener(Protocol):
    def on_stale(self, instrument_id: str, silent_for: timedelta) -> None: ...

    def on_recovered(self, instrument_id: str) -> None: ...


@dataclass(frozen=True)
class StalenessChange:
    instrument_id: str
    stale: bool


class StalenessWatchdog:
    """A `TickSubscriber`, a `ConnectionListener` and a `StalenessView`."""

    def __init__(
        self,
        clock: Clock,
        policy: ThresholdPolicy | None = None,
        window: SessionWindow | None = None,
        alerts: AlertSink | None = None,
    ) -> None:
        self._clock = clock
        self._policy = policy or TieredThresholds()
        self._window = window or SessionWindow()
        self._alerts = alerts
        self._last_tick: dict[str, datetime] = {}
        self._watched: set[str] = set()
        self._reported_stale: set[str] = set()
        self._listeners: list[StalenessListener] = []
        self._feed_down = False
        self._feed_down_since: datetime | None = None
        self._ticks_seen_on: set[date] = set()
        self._no_tick_alarm_on: set[date] = set()

    def add_listener(self, listener: StalenessListener) -> None:
        self._listeners.append(listener)

    # -- what to watch ----------------------------------------------------------------------

    def watch(self, instrument_ids: Iterable[str]) -> None:
        self._watched.update(instrument_ids)

    def unwatch(self, instrument_ids: Iterable[str]) -> None:
        for instrument_id in instrument_ids:
            self._watched.discard(instrument_id)
            self._reported_stale.discard(instrument_id)
            self._last_tick.pop(instrument_id, None)

    # -- inputs -----------------------------------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        self._last_tick[tick.instrument_id] = tick.received_ts
        self._ticks_seen_on.add(tick.received_ts.astimezone(IST).date())
        if tick.instrument_id in self._reported_stale and not self.is_stale(tick.instrument_id):
            self._recover(tick.instrument_id)

    async def on_connected(self) -> None:
        self._feed_down = False
        self._feed_down_since = None

    async def on_disconnected(self, reason: str) -> None:
        if not self._feed_down:
            self._feed_down_since = self._clock.now()
        self._feed_down = True

    # -- the view the risk engine reads ------------------------------------------------------

    def silent_for(self, instrument_id: str) -> timedelta | None:
        """How long the instrument has been quiet, measured from the session open at the
        earliest; `None` outside the session, where silence means nothing."""
        now = self._clock.now()
        if not self._window.contains(now):
            return None
        opened = self._window.open_at(now.astimezone(IST).date())
        last = max(self._last_tick.get(instrument_id, opened), opened)
        return max(now - last, timedelta(0))

    def is_stale(self, instrument_id: str) -> bool:
        if instrument_id not in self._watched:
            return False
        if self._feed_down and self._window.contains(self._clock.now()):
            return True
        quiet = self.silent_for(instrument_id)
        return quiet is not None and quiet > self._policy.threshold(instrument_id)

    def feed_down_since(self) -> datetime | None:
        """When the feed dropped, measured from the session open at the earliest; `None` while it
        is connected or outside the session (overnight silence is not an outage)."""
        now = self._clock.now()
        if not self._feed_down or self._feed_down_since is None or not self._window.contains(now):
            return None
        return max(self._feed_down_since, self._window.open_at(now.astimezone(IST).date()))

    def watched_count(self) -> int:
        return len(self._watched)

    def stale_count(self) -> int:
        return len(self.stale_instruments())

    def stale_instruments(self) -> frozenset[str]:
        return frozenset(i for i in self._watched if self.is_stale(i))

    def permits_entry(self, instrument_id: str) -> bool:
        return not self.is_stale(instrument_id)

    def permits_exit(self, instrument_id: str) -> bool:
        """Always true: stale data must never trap a position."""
        return True

    # -- transitions ------------------------------------------------------------------------

    def check(self) -> list[StalenessChange]:
        """Notice instruments that just went stale (and any that recovered without a tick, e.g.
        after the feed came back), raising one alert per transition. Run about once a second."""
        changes: list[StalenessChange] = []
        for instrument_id in sorted(self._watched):
            stale = self.is_stale(instrument_id)
            if stale and instrument_id not in self._reported_stale:
                self._go_stale(instrument_id)
                changes.append(StalenessChange(instrument_id, True))
            elif not stale and instrument_id in self._reported_stale:
                self._recover(instrument_id)
                changes.append(StalenessChange(instrument_id, False))
        self._check_market_open()
        return changes

    def _go_stale(self, instrument_id: str) -> None:
        self._reported_stale.add(instrument_id)
        quiet = self.silent_for(instrument_id) or timedelta(0)
        _LOG.warning(
            "%s is stale (silent %.0fs): entries blocked, exits allowed",
            instrument_id,
            quiet.total_seconds(),
        )
        self._alert("market_data.stale", f"{instrument_id} silent for {quiet.total_seconds():.0f}s")
        for listener in self._listeners:
            listener.on_stale(instrument_id, quiet)

    def _recover(self, instrument_id: str) -> None:
        self._reported_stale.discard(instrument_id)
        _LOG.info("%s recovered", instrument_id)
        for listener in self._listeners:
            listener.on_recovered(instrument_id)

    def _check_market_open(self) -> None:
        """No tick from anywhere a minute after the open: a broken feed or a stale calendar."""
        now = self._clock.now()
        today = now.astimezone(IST).date()
        if not self._window.contains(now) or today in self._no_tick_alarm_on:
            return
        if now < self._window.open_at(today) + timedelta(minutes=1) or today in self._ticks_seen_on:
            return
        self._no_tick_alarm_on.add(today)
        self._alert("market_data.no_ticks_at_open", "no ticks by 09:16 on an expected trading day")

    def _alert(self, name: str, message: str) -> None:
        if self._alerts is not None:
            self._alerts.raise_alert(name, message)
