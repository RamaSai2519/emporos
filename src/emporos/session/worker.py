"""The trading worker: ONE process, one session, driven by a deterministic poll loop.

    STARTING → AUTHENTICATING → RECOVERING → CONNECTING → READY → TRADING ⇄ HALTED
                  → SQUARING_OFF → RECONCILING → REPORTING → SHUTTING_DOWN     (any → FAILED)

Each poll: honour the kill switch, hand strategies what arrived, run the jobs that are due. Nothing
is trading until recovery has resolved every doubtful order and the books reconcile clean; a dirty
recovery leaves the session HALTED, waiting for an operator to resume, which triggers a fresh
recovery before trading restarts. The worker holds no broker credentials of its own: everything it
does to a broker goes through the execution engine it was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import IST, Clock, Sleeper
from emporos.persistence.records import OrderRecord, PositionRecord
from emporos.portfolio.reconciliation import ReconciliationTrigger
from emporos.session.close_out import CloseOutResult, EndOfDay
from emporos.session.host import StrategyHost
from emporos.session.jobs import JobScheduler
from emporos.session.lifecycle import SessionLifecycle, SessionState
from emporos.session.recovery import RecoveryResult, StartupRecovery
from emporos.session.square_off import SquareOffReport, SquareOffService


@dataclass(frozen=True)
class SessionSchedule:
    """Wall-clock (IST) moments of the trading day."""

    square_off_at: time = time(15, 15)
    close_at: time = time(15, 30)
    poll_interval: timedelta = timedelta(seconds=1)
    resume_retry: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if not self.square_off_at < self.close_at:
            raise ValueError("square-off must come before the close")
        if self.poll_interval <= timedelta(0) or self.resume_retry <= timedelta(0):
            raise ValueError("intervals must be positive")

    def _at(self, now: datetime, when: time) -> datetime:
        return datetime.combine(now.astimezone(IST).date(), when, tzinfo=IST)

    def square_off(self, now: datetime) -> datetime:
        return self._at(now, self.square_off_at)

    def close(self, now: datetime) -> datetime:
        return self._at(now, self.close_at)


class KillSwitchView(Protocol):
    async def halted(self) -> bool:
        """Read the kill switch NOW. True when it is set OR cannot be read (unknown is halted)."""
        ...


class SessionVenue(Protocol):
    """Everything about reaching the outside world that is not an order: log in, open feeds."""

    async def authenticate(self) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class EventFlush(Protocol):
    async def flush(self) -> object:
        """Make every queued alert and state change durable."""
        ...


class BooksView(Protocol):
    async def open_positions(self) -> list[PositionRecord]: ...
    async def active(self) -> list[OrderRecord]: ...


@dataclass(frozen=True)
class SessionReport:
    final_state: SessionState
    traded: bool
    recovery: RecoveryResult | None
    square_off: SquareOffReport | None
    flat_at_close: bool | None
    close_out: CloseOutResult | None
    failure: str = ""


class TradingWorker:
    def __init__(
        self,
        lifecycle: SessionLifecycle,
        schedule: SessionSchedule,
        clock: Clock,
        sleeper: Sleeper,
        alerts: AlertSink,
        venue: SessionVenue,
        recovery: StartupRecovery,
        host: StrategyHost,
        switch: KillSwitchView,
        always: JobScheduler,
        while_trading: JobScheduler,
        square_off: SquareOffService,
        books: BooksView,
        end_of_day: EndOfDay,
        events: EventFlush,
    ) -> None:
        self._lifecycle = lifecycle
        self._schedule = schedule
        self._clock = clock
        self._sleeper = sleeper
        self._alerts = alerts
        self._venue = venue
        self._recovery = recovery
        self._host = host
        self._switch = switch
        self._always = always
        self._while_trading = while_trading
        self._square_off = square_off
        self._books = books
        self._end_of_day = end_of_day
        self._events = events
        self._connected = False
        self._traded = False
        self._last_resume: datetime | None = None
        self._recovery_result: RecoveryResult | None = None

    @property
    def state(self) -> SessionState:
        return self._lifecycle.state

    async def run_session(self) -> SessionReport:
        squared: SquareOffReport | None = None
        flat: bool | None = None
        closed: CloseOutResult | None = None
        try:
            await self._start()
            await self._trade()
            if self._lifecycle.state is SessionState.TRADING:
                squared, flat = await self._flatten()
            self._lifecycle.transition(SessionState.RECONCILING, "session over")
            closed = await self._end_of_day.close_out()
            self._lifecycle.transition(SessionState.REPORTING, "reconciled")
            await self._events.flush()
            self._lifecycle.transition(SessionState.SHUTTING_DOWN, "reported")
        except Exception as error:
            self._alerts.raise_alert("session_failed", repr(error))
            self._lifecycle.transition(SessionState.FAILED, repr(error))
            return SessionReport(
                SessionState.FAILED, self._traded, self._recovery_result, squared, flat, closed,
                f"{type(error).__name__}: {error}",
            )  # fmt: skip
        finally:
            await self._shutdown()
            await self._events.flush()
        return SessionReport(
            self._lifecycle.state, self._traded, self._recovery_result, squared, flat, closed
        )

    # --- phases ----------------------------------------------------------------------------
    async def _start(self) -> None:
        self._lifecycle.transition(SessionState.AUTHENTICATING)
        await self._venue.authenticate()
        self._lifecycle.transition(SessionState.RECOVERING)
        self._recovery_result = await self._recovery.run()
        if not self._recovery_result.clean:
            self._lifecycle.transition(SessionState.HALTED, self._recovery_result.problem)
            return
        await self._begin_trading()

    async def _begin_trading(self) -> None:
        """Connect (once), start strategies (once) and trade. Reached from a clean recovery, and
        again after an operator's resume passes a fresh recovery."""
        resuming = self._lifecycle.state is SessionState.HALTED
        if not resuming:
            self._lifecycle.transition(SessionState.CONNECTING)
        if not self._connected:
            await self._venue.connect()
            await self._host.start()
            self._connected = True
        if not resuming:
            self._lifecycle.transition(SessionState.READY)
        self._lifecycle.transition(SessionState.TRADING, "recovered and reconciled")
        self._traded = True

    async def _trade(self) -> None:
        while self._clock.now() < self._schedule.square_off(self._clock.now()):
            await self._poll()
            await self._sleeper.sleep(self._schedule.poll_interval.total_seconds())

    async def _poll(self) -> None:
        state = self._lifecycle.state
        halted = await self._switch.halted()
        if state is SessionState.TRADING and halted:
            self._lifecycle.transition(SessionState.HALTED, "kill switch")
        elif state is SessionState.HALTED and not halted:
            await self._try_resume()
        if self._lifecycle.state is SessionState.TRADING:
            await self._host.pump()
        await self._always.run_due()
        if self._lifecycle.state is SessionState.TRADING:
            await self._while_trading.run_due()

    async def _try_resume(self) -> None:
        now = self._clock.now()
        if self._last_resume is not None and now - self._last_resume < self._schedule.resume_retry:
            return
        self._last_resume = now
        result = await self._recovery.run(ReconciliationTrigger.MANUAL)
        self._recovery_result = result
        if result.clean:
            await self._begin_trading()

    async def _flatten(self) -> tuple[SquareOffReport, bool]:
        self._lifecycle.transition(SessionState.SQUARING_OFF, "square-off time")
        await self._host.end_session()
        report = await self._square_off.flatten("end of session square-off")
        while self._clock.now() < self._schedule.close(self._clock.now()):
            await self._always.run_due()
            await self._while_trading.run_due()
            if await self._is_flat():
                return report, True
            await self._sleeper.sleep(self._schedule.poll_interval.total_seconds())
        self._alerts.raise_alert("square_off_incomplete", "positions or orders remain at the close")
        return report, False

    async def _is_flat(self) -> bool:
        return not await self._books.open_positions() and not await self._books.active()

    async def _shutdown(self) -> None:
        try:
            await self._host.shutdown()
        finally:
            await self._venue.close()
