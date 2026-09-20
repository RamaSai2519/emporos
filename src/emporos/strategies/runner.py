"""`StrategyRunner` — drives ONE strategy through its lifecycle and forwards its signals.

    feed ──▶ runner ──▶ strategy.on_market_data ──▶ strategy.generate_signal ──▶ SignalSink

The runner never sees a broker: it takes events from an injected feed, hands signals to an
injected sink, and tells an injected `AlertSink` when it has had to stop the strategy.

Isolation (CLAUDE.md): a strategy handler that raises halts THAT strategy and raises an alert; it
never propagates, so it cannot take the session down. A halted runner ignores further events but
still calls `on_shutdown`. A failing SINK is different — that is infrastructure, not the
strategy — so its error propagates: a signal that could not be recorded must not vanish quietly.

Look-ahead safety (plan.md §10): a bar is delivered only once it has CLOSED at the context clock's
time. In replay the `ClockSync` moves the clock to the bar's close; live, it is a no-op and the
wall clock is what it is. A bar the feed hands over early is skipped and counted, never delivered.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.signals import Signal, SignalSink
from emporos.domain.ticks import Tick
from emporos.strategies.base import Strategy
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import BarRecorder

MarketEvent = Candle | Tick
MarketFeed = AsyncIterable[MarketEvent]

# A strategy that never stops returning signals is broken, not prolific.
MAX_SIGNALS_PER_EVENT = 100


class ClockSync(Protocol):
    """Brings the context clock to the moment an event happened."""

    def sync_to(self, when: datetime) -> None: ...


class WallClockSync:
    """Live and paper: the clock is the real one; there is nothing to move."""

    def sync_to(self, when: datetime) -> None:
        return None


class SettableClock(Clock, Protocol):
    def set(self, when: datetime) -> None: ...


class ReplayClockSync:
    """Replay and backtest: the clock stands at the latest event seen and never runs backwards."""

    def __init__(self, clock: SettableClock) -> None:
        self._clock = clock

    def sync_to(self, when: datetime) -> None:
        if when > self._clock.now():
            self._clock.set(when)


class RunnerState(StrEnum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    ENDED = "ENDED"  # the session is over; only shutdown remains
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class RunReport:
    signals_emitted: int
    unclosed_bars_skipped: int
    halted: bool
    halt_reason: str | None


class StrategyRunner:
    def __init__(
        self,
        strategy: Strategy,
        context: StrategyContext,
        recorder: BarRecorder,
        sink: SignalSink,
        clock_sync: ClockSync,
        alerts: AlertSink,
    ) -> None:
        self._strategy = strategy
        self._ctx = context
        self._recorder = recorder
        self._sink = sink
        self._clock_sync = clock_sync
        self._alerts = alerts
        self._state = RunnerState.NEW
        self._halt_reason: str | None = None
        self._signals_emitted = 0
        self._unclosed_skipped = 0

    @property
    def state(self) -> RunnerState:
        return self._state

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def report(self) -> RunReport:
        return RunReport(
            self._signals_emitted, self._unclosed_skipped, self.halted, self._halt_reason
        )

    async def run(self, feed: MarketFeed) -> RunReport:
        """Replay convenience: start, deliver every event, end the session, shut down."""
        await self.start()
        async for event in feed:
            await self.handle(event)
        await self.end_session()
        await self.shutdown()
        return self.report()

    async def start(self) -> None:
        if self._state is not RunnerState.NEW:
            raise RuntimeError(f"cannot start a runner that is {self._state}")
        self._state = RunnerState.RUNNING
        self._guard("initialize", lambda: self._strategy.initialize(self._ctx))

    async def handle(self, event: MarketEvent) -> None:
        if not self._accepting():
            return
        if isinstance(event, Candle):
            self._clock_sync.sync_to(event.closes_at)
            if event.closes_at > self._ctx.clock.now():
                self._skip_unclosed(event)
                return
            self._recorder.record(event)
        else:
            self._clock_sync.sync_to(event.exchange_ts)
        self._guard("on_market_data", lambda: self._strategy.on_market_data(event))
        await self._forward_signals()

    async def handle_order_update(self, update: OrderUpdate) -> None:
        if not self._accepting():
            return
        self._guard("on_order_update", lambda: self._strategy.on_order_update(update))
        await self._forward_signals()

    async def end_session(self) -> None:
        if self._state is not RunnerState.RUNNING:
            return
        self._state = RunnerState.ENDED
        if not self.halted:
            self._guard("on_session_end", self._strategy.on_session_end)
            await self._forward_signals()

    async def begin_session(self) -> None:
        """The next trading day, after `end_session()`: deliver events again. A run that spans
        days (a backtest) ends and begins a session per day; a halted strategy stays halted."""
        if self._state is RunnerState.ENDED:
            self._state = RunnerState.RUNNING

    async def shutdown(self) -> None:
        if self._state is RunnerState.STOPPED:
            return
        self._state = RunnerState.STOPPED
        try:
            self._strategy.on_shutdown()
        except Exception:
            self._ctx.logger.exception("strategy on_shutdown raised")

    def _accepting(self) -> bool:
        return self._state is RunnerState.RUNNING and not self.halted

    def _skip_unclosed(self, bar: Candle) -> None:
        self._unclosed_skipped += 1
        self._ctx.logger.warning(
            "bar %s %s %s has not closed yet at %s: not delivered",
            bar.instrument_id,
            bar.timeframe,
            bar.ts.isoformat(),
            self._ctx.clock.now().isoformat(),
        )

    async def _forward_signals(self) -> None:
        for signal in self._pull_signals():
            await self._sink.submit(signal)
            self._signals_emitted += 1

    def _pull_signals(self) -> list[Signal]:
        """Drain the strategy. A fault or a contract breach halts it; signals that were already
        valid are still forwarded."""
        pulled: list[Signal] = []
        for _ in range(MAX_SIGNALS_PER_EVENT):
            signal = self._next_signal()
            if signal is None:
                return pulled
            pulled.append(signal)
        self._halt("generate_signal", f"returned more than {MAX_SIGNALS_PER_EVENT} signals at once")
        return pulled

    def _next_signal(self) -> Signal | None:
        if self.halted:
            return None
        try:
            signal = self._strategy.generate_signal()
        except Exception as error:
            self._halt("generate_signal", repr(error))
            return None
        if signal is None:
            return None
        breach = self._contract_breach(signal)
        if breach is not None:
            self._halt("generate_signal", breach)
            return None
        return signal

    def _contract_breach(self, signal: object) -> str | None:
        if not isinstance(signal, Signal):
            return f"returned {type(signal).__name__}, not a Signal"
        if signal.strategy_run_id != self._ctx.run_id:
            return f"signal names run {signal.strategy_run_id}, not {self._ctx.run_id}"
        if signal.instrument_id not in self._ctx.config.instrument_ids:
            return f"signal for {signal.instrument_id}, which is not in the universe"
        return None

    def _guard(self, what: str, action: Callable[[], object]) -> bool:
        try:
            action()
        except Exception as error:
            self._halt(what, repr(error))
            return False
        return True

    def _halt(self, what: str, why: str) -> None:
        self._halt_reason = f"{what}: {why}"
        self._ctx.logger.error("strategy %s halted: %s", self._strategy.name, self._halt_reason)
        self._alerts.raise_alert(
            "strategy_halted", f"{self._strategy.name} run {self._ctx.run_id}: {self._halt_reason}"
        )
