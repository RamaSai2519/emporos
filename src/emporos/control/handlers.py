"""What each command DOES. Every handler is idempotent, because a command interrupted by a restart
is re-run.

Order-affecting commands (SQUARE_OFF_ALL, CLOSE_POSITION, PLACE_MANUAL_ORDER) go through the same
gated signal path as a strategy's signal: recorded, risk-reviewed by every rule, then executed. A
manual order is not privileged — it is refused by exactly the rules that would refuse a strategy
signal — and it carries a signal id derived from the command id, so replaying the command returns
the order it already placed instead of placing another.
"""

from __future__ import annotations

from typing import Any, Protocol

from emporos.control.commands import (
    CancelOrderParams,
    ClosePositionParams,
    NoParams,
    Params,
    PlaceManualOrderParams,
    RunBacktestParams,
    SetKillSwitchParams,
    SetTradingModeParams,
    SquareOffAllParams,
    StartStrategyParams,
    StrategyNameParams,
    TriggerBackfillParams,
    UpdateStrategyConfigParams,
)
from emporos.control.processor import Outcome
from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.persistence.records import CommandRecord, OrderRecord, PositionRecord
from emporos.risk.approval import RiskRejection
from emporos.risk.kill_switch import SinkOutcome
from emporos.session.lifecycle import SessionState
from emporos.session.signal_path import Submission
from emporos.session.square_off import SquareOffReport

MANUAL_RUN = "manual"


class KillSwitchPort(Protocol):
    async def engage(self, reason: str, set_by: str) -> list[SinkOutcome]: ...

    async def release(self, set_by: str) -> list[SinkOutcome]: ...


class SquareOffPort(Protocol):
    async def flatten(self, reason: str, only: str | None = None) -> SquareOffReport: ...


class CancelPort(Protocol):
    async def cancel(self, order_id: str) -> OrderRecord: ...


class ManualSignalPort(Protocol):
    async def submit_as(self, signal: Signal, signal_id: str | None = None) -> Submission: ...


class OpenPositions(Protocol):
    async def open_positions(self) -> list[PositionRecord]: ...


class ReconcilePort(Protocol):
    async def run_now(self) -> int:
        """Reconcile immediately; returns the number of discrepancies found."""
        ...


class StartGate(Protocol):
    async def check(self, name: str, acknowledged: str | None) -> str | None:
        """Why this strategy may not start now, or None if it may."""
        ...


class OpenStartGate:
    """Lets every start through. For tests and deployments with no verdicts to consult."""

    async def check(self, name: str, acknowledged: str | None) -> str | None:
        return None


class StrategyControlPort(Protocol):
    async def start(self, name: str) -> str: ...

    async def stop(self, name: str) -> str: ...

    async def store_config(self, name: str, config: dict[str, Any]) -> str: ...


class JobPort(Protocol):
    async def start(self, kind: str, command_id: str, params: dict[str, Any]) -> str: ...


class SessionView(Protocol):
    @property
    def state(self) -> SessionState: ...


def _written(outcomes: list[SinkOutcome]) -> str:
    return ", ".join(f"{o.name}:{'ok' if o.ok else 'FAILED'}" for o in outcomes)


class KillSwitchHandler:
    def __init__(self, control: KillSwitchPort) -> None:
        self._control = control

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, SetKillSwitchParams)
        if params.halted:
            if not params.reason.strip():
                return Outcome.rejected("a halt must say why")
            outcomes = await self._control.engage(params.reason, command.issued_by)
        else:
            outcomes = await self._control.release(command.issued_by)
        if not any(o.ok for o in outcomes):
            return Outcome.failed(f"kill switch could not be written: {_written(outcomes)}")
        return Outcome.done(_written(outcomes))


class SquareOffAllHandler:
    def __init__(self, square_off: SquareOffPort) -> None:
        self._square_off = square_off

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, SquareOffAllParams)
        report = await self._square_off.flatten(params.reason)
        return _square_off_outcome(report)


class ClosePositionHandler:
    def __init__(self, square_off: SquareOffPort) -> None:
        self._square_off = square_off

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, ClosePositionParams)
        report = await self._square_off.flatten(params.reason, only=params.instrument_id)
        if not (report.submitted or report.already_closing or report.unpriced):
            return Outcome.done("no open position in that instrument")
        return _square_off_outcome(report)


def _square_off_outcome(report: SquareOffReport) -> Outcome:
    data = {
        "submitted": list(report.submitted),
        "already_closing": list(report.already_closing),
        "unpriced": list(report.unpriced),
    }
    if report.unpriced:
        return Outcome.failed("no live price for: " + ", ".join(report.unpriced), **data)
    return Outcome.done(f"exit orders sent for {len(report.submitted)} position(s)", **data)


class CancelOrderHandler:
    def __init__(self, engine: CancelPort) -> None:
        self._engine = engine

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, CancelOrderParams)
        try:
            order = await self._engine.cancel(params.order_id)
        except ValueError as error:
            return Outcome.rejected(str(error))
        return Outcome.done(f"order is {order.state}", state=order.state)  # a no-op if terminal


class PlaceManualOrderHandler:
    def __init__(self, signals: ManualSignalPort, positions: OpenPositions, clock: Clock) -> None:
        self._signals = signals
        self._positions = positions
        self._clock = clock

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, PlaceManualOrderParams)
        held = {p.instrument_id: p.net_quantity for p in await self._positions.open_positions()}
        net = held.get(params.instrument_id, 0)
        reduces = (net > 0 and params.side is OrderSide.SELL) or (
            net < 0 and params.side is OrderSide.BUY
        )
        signal = Signal(
            strategy_run_id=MANUAL_RUN,
            instrument_id=params.instrument_id,
            kind=SignalKind.EXIT if reduces else SignalKind.ENTRY,
            side=params.side,
            order_type=OrderType.LIMIT,
            quantity=params.quantity,
            limit_price=Money(params.limit_price),
            ts=self._clock.now(),
            reason=f"MANUAL by {command.issued_by}: {params.reason}",
        )
        result = await self._signals.submit_as(signal, f"manual-{command.id}")
        if result.rejection is not None:
            return _risk_rejected(result.rejection, result.signal_id)
        if result.order is None:
            return Outcome.rejected(
                f"execution refused: {result.refusal}", signal_id=result.signal_id
            )
        return Outcome.done(
            f"order {result.order.state}", order_id=result.order.id, signal_id=result.signal_id
        )


def _risk_rejected(rejection: RiskRejection, signal_id: str) -> Outcome:
    return Outcome.rejected(
        f"{rejection.rule}: {rejection.reason}", rule=rejection.rule, signal_id=signal_id
    )


class StartStrategyHandler:
    """A start is refused, with the reason, when the gate says so: the check is the worker's, so it
    holds however the command arrived (dashboard, API client, replayed after a restart)."""

    def __init__(self, strategies: StrategyControlPort, gate: StartGate) -> None:
        self._strategies = strategies
        self._gate = gate

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, StartStrategyParams)
        refusal = await self._gate.check(params.name, params.acknowledge)
        if refusal is not None:
            return Outcome.rejected(refusal)
        return Outcome.done(await self._strategies.start(params.name))


class StopStrategyHandler:
    """Stop is graceful: the strategy is told and stops signalling. Its positions are NOT closed
    unless the operator asks (CLOSE_POSITION / SQUARE_OFF_ALL)."""

    def __init__(self, strategies: StrategyControlPort) -> None:
        self._strategies = strategies

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, StrategyNameParams)
        return Outcome.done(await self._strategies.stop(params.name))


class UpdateStrategyConfigHandler:
    """Schema-validated by the strategy layer; rejected mid-session unless forced; takes effect at
    the strategy's next start (a running strategy is never re-configured under its own feet)."""

    _MID_SESSION = (SessionState.TRADING, SessionState.HALTED, SessionState.SQUARING_OFF)

    def __init__(self, strategies: StrategyControlPort, session: SessionView) -> None:
        self._strategies = strategies
        self._session = session

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, UpdateStrategyConfigParams)
        if self._session.state in self._MID_SESSION and not params.force:
            return Outcome.rejected("the session is running: resend with force to store it anyway")
        try:
            return Outcome.done(await self._strategies.store_config(params.name, params.config))
        except ValueError as error:
            return Outcome.rejected(f"invalid strategy configuration: {error}")


class ReconcileNowHandler:
    def __init__(self, reconciler: ReconcilePort) -> None:
        self._reconciler = reconciler

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, NoParams)
        found = await self._reconciler.run_now()
        if found:
            return Outcome.done(f"{found} discrepancies found; trading halted", discrepancies=found)
        return Outcome.done("books agree with the broker", discrepancies=0)


class TradingModeHandler:
    """Paper ↔ live is a DEPLOYMENT action (plan.md §20), never a runtime toggle."""

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, SetTradingModeParams)
        return Outcome.rejected(
            "the trading mode is fixed at deployment and cannot be changed here"
        )


class BackfillHandler:
    def __init__(self, jobs: JobPort) -> None:
        self._jobs = jobs

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, TriggerBackfillParams)
        try:
            job = await self._jobs.start("backfill", command.id, params.model_dump(mode="json"))
        except LookupError as error:
            return Outcome.rejected(str(error))
        return Outcome.done(f"backfill started: {job}", job_id=job)


class BacktestHandler:
    def __init__(self, jobs: JobPort) -> None:
        self._jobs = jobs

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        assert isinstance(params, RunBacktestParams)
        if params.end < params.start:
            return Outcome.rejected("the end date is before the start date")
        try:
            job = await self._jobs.start("backtest", command.id, params.model_dump(mode="json"))
        except LookupError as error:
            return Outcome.rejected(str(error))
        return Outcome.done(f"backtest started: {job}", job_id=job)
