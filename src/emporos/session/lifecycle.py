"""The session lifecycle (plan.md §13) as an explicit, validated state machine.

    STARTING → AUTHENTICATING → RECOVERING → CONNECTING → READY → TRADING ⇄ HALTED
                    → SQUARING_OFF → RECONCILING → REPORTING → SHUTTING_DOWN       (any → FAILED)

Trading is only reachable through RECOVERING: nothing is traded before every doubtful order has
been resolved and the books reconciled. A HALTED session cannot square off (the kill switch blocks
every order, exits included): it is resumed by an operator, or goes on to reconcile and report.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from emporos.core.clock import Clock


class SessionState(StrEnum):
    STARTING = "STARTING"
    AUTHENTICATING = "AUTHENTICATING"
    RECOVERING = "RECOVERING"
    CONNECTING = "CONNECTING"
    READY = "READY"
    TRADING = "TRADING"
    HALTED = "HALTED"
    SQUARING_OFF = "SQUARING_OFF"
    RECONCILING = "RECONCILING"
    REPORTING = "REPORTING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    FAILED = "FAILED"


_S = SessionState
_ALLOWED: dict[SessionState, frozenset[SessionState]] = {
    _S.STARTING: frozenset({_S.AUTHENTICATING}),
    _S.AUTHENTICATING: frozenset({_S.RECOVERING}),
    _S.RECOVERING: frozenset({_S.CONNECTING, _S.HALTED}),
    _S.CONNECTING: frozenset({_S.READY}),
    _S.READY: frozenset({_S.TRADING, _S.HALTED}),
    _S.TRADING: frozenset({_S.HALTED, _S.SQUARING_OFF}),
    _S.HALTED: frozenset({_S.TRADING, _S.RECONCILING}),
    _S.SQUARING_OFF: frozenset({_S.RECONCILING, _S.HALTED}),
    _S.RECONCILING: frozenset({_S.REPORTING}),
    _S.REPORTING: frozenset({_S.SHUTTING_DOWN}),
    _S.SHUTTING_DOWN: frozenset(),
    _S.FAILED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    """The session tried to move somewhere the lifecycle does not allow."""


@dataclass(frozen=True)
class StateChange:
    previous: SessionState
    current: SessionState
    reason: str
    at: datetime


class SessionLifecycle:
    def __init__(
        self, clock: Clock, listeners: tuple[Callable[[StateChange], None], ...] = ()
    ) -> None:
        self._clock = clock
        self._listeners = listeners
        self._state = SessionState.STARTING

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def trading_allowed(self) -> bool:
        return self._state is SessionState.TRADING

    def transition(self, target: SessionState, reason: str = "") -> StateChange:
        if target is not SessionState.FAILED and target not in _ALLOWED[self._state]:
            raise IllegalTransitionError(f"{self._state.value} cannot become {target.value}")
        if self._state is SessionState.FAILED:
            raise IllegalTransitionError("a failed session goes nowhere")
        change = StateChange(self._state, target, reason, self._clock.now())
        self._state = target
        for listener in self._listeners:
            listener(change)
        return change
