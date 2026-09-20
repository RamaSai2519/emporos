"""The order state machine (plan.md §12): which state may follow which, and what fills allow.

`MODIFY` is deliberately absent. Repricing is cancel-then-replace, never modify-in-place, because a
modify racing a fill is ambiguous in a way cancel-and-confirm is not (EM-99 F5). `RISK_REJECTED`
is also not an order state: a rejected signal never becomes an order, so it lives in `risk_events`.
A terminal order can still record a late fill (state unchanged, quantity up) — a fill that
arrived after a cancel is a fact, not a transition.
"""

from enum import StrEnum


class OrderState(StrEnum):
    PENDING_NEW = "PENDING_NEW"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    UNKNOWN = "UNKNOWN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELLED, self.REJECTED}


_S = OrderState
# What may follow each state. A state may always be followed by itself (a further fill, or another
# absence check while UNKNOWN); terminal states allow nothing else.
_ALLOWED: dict[OrderState, frozenset[OrderState]] = {
    _S.PENDING_NEW: frozenset(
        {_S.OPEN, _S.UNKNOWN, _S.REJECTED, _S.PARTIALLY_FILLED, _S.FILLED, _S.CANCELLED}
    ),
    _S.UNKNOWN: frozenset({_S.OPEN, _S.PARTIALLY_FILLED, _S.FILLED, _S.CANCELLED, _S.REJECTED}),
    _S.OPEN: frozenset(
        {_S.PARTIALLY_FILLED, _S.FILLED, _S.PENDING_CANCEL, _S.CANCELLED, _S.REJECTED, _S.UNKNOWN}
    ),
    _S.PARTIALLY_FILLED: frozenset({_S.FILLED, _S.PENDING_CANCEL, _S.CANCELLED, _S.UNKNOWN}),
    _S.PENDING_CANCEL: frozenset(
        {_S.CANCELLED, _S.FILLED, _S.PARTIALLY_FILLED, _S.OPEN, _S.UNKNOWN}
    ),
    _S.FILLED: frozenset(),
    _S.CANCELLED: frozenset(),
    _S.REJECTED: frozenset(),
}


class OrderStateMachine:
    """Reject impossible transitions and impossible cumulative fill quantities."""

    def allowed_from(self, state: OrderState) -> frozenset[OrderState]:
        return _ALLOWED[state] | {state}

    def validate(
        self,
        previous: OrderState,
        target: OrderState,
        previous_filled: int,
        filled: int,
        quantity: int,
    ) -> None:
        if not 0 <= previous_filled <= filled <= quantity:
            raise ValueError("cumulative fills must increase within the order quantity")
        if target not in self.allowed_from(previous):
            raise ValueError(f"{previous.value} cannot become {target.value}")
        if target == OrderState.FILLED and filled != quantity:
            raise ValueError("FILLED requires the entire quantity")
        if target == OrderState.PARTIALLY_FILLED and not 0 < filled < quantity:
            raise ValueError("PARTIALLY_FILLED requires an incomplete positive fill")
        if target in (OrderState.OPEN, OrderState.PENDING_NEW) and filled:
            raise ValueError("an order with fills cannot be OPEN or PENDING_NEW")
        if target == OrderState.REJECTED and filled:
            raise ValueError("an order with fills was not rejected")
