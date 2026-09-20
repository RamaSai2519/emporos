"""Every transition in plan.md §12 is reachable; every other one is refused."""

from itertools import product

import pytest

from emporos.execution.state import OrderState, OrderStateMachine

S = OrderState
MACHINE = OrderStateMachine()

REACHABLE = {
    (S.PENDING_NEW, S.OPEN), (S.PENDING_NEW, S.UNKNOWN), (S.PENDING_NEW, S.REJECTED),
    (S.PENDING_NEW, S.PARTIALLY_FILLED), (S.PENDING_NEW, S.FILLED), (S.PENDING_NEW, S.CANCELLED),
    (S.UNKNOWN, S.OPEN), (S.UNKNOWN, S.PARTIALLY_FILLED), (S.UNKNOWN, S.FILLED),
    (S.UNKNOWN, S.CANCELLED), (S.UNKNOWN, S.REJECTED),
    (S.OPEN, S.PARTIALLY_FILLED), (S.OPEN, S.FILLED), (S.OPEN, S.PENDING_CANCEL),
    (S.OPEN, S.CANCELLED), (S.OPEN, S.REJECTED), (S.OPEN, S.UNKNOWN),
    (S.PARTIALLY_FILLED, S.FILLED), (S.PARTIALLY_FILLED, S.PENDING_CANCEL),
    (S.PARTIALLY_FILLED, S.CANCELLED), (S.PARTIALLY_FILLED, S.UNKNOWN),
    (S.PENDING_CANCEL, S.CANCELLED), (S.PENDING_CANCEL, S.FILLED),
    (S.PENDING_CANCEL, S.PARTIALLY_FILLED), (S.PENDING_CANCEL, S.OPEN),
    (S.PENDING_CANCEL, S.UNKNOWN),
}  # fmt: skip


def quantities(target: OrderState) -> tuple[int, int, int]:
    """(filled before, filled after, order size) that suit the target state's own rules."""
    if target in (S.FILLED,):
        return 0, 10, 10
    if target in (S.PARTIALLY_FILLED,):
        return 0, 4, 10
    return 0, 0, 10


@pytest.mark.parametrize(("old", "new"), sorted(REACHABLE))
def test_every_valid_transition_is_accepted(old: OrderState, new: OrderState) -> None:
    before, after, size = quantities(new)
    MACHINE.validate(old, new, before, after, size)


@pytest.mark.parametrize(
    ("old", "new"),
    [(o, n) for o, n in product(S, S) if o != n and (o, n) not in REACHABLE],
)
def test_every_other_transition_is_refused(old: OrderState, new: OrderState) -> None:
    before, after, size = quantities(new)
    with pytest.raises(ValueError):
        MACHINE.validate(old, new, before, after, size)


@pytest.mark.parametrize("state", [S.FILLED, S.CANCELLED, S.REJECTED])
def test_a_terminal_state_leads_nowhere(state: OrderState) -> None:
    assert state.terminal and MACHINE.allowed_from(state) == {state}


def test_a_late_fill_may_raise_the_quantity_of_a_cancelled_order() -> None:
    MACHINE.validate(S.CANCELLED, S.CANCELLED, 0, 4, 10)


@pytest.mark.parametrize(
    ("old", "new", "before", "after", "size"),
    [
        (S.OPEN, S.FILLED, 0, 4, 10),  # FILLED needs everything
        (S.OPEN, S.FILLED, 0, 11, 10),  # more than the order
        (S.OPEN, S.PARTIALLY_FILLED, 0, 0, 10),  # partial needs something
        (S.OPEN, S.PARTIALLY_FILLED, 0, 10, 10),  # ...but not everything
        (S.PENDING_CANCEL, S.OPEN, 4, 4, 10),  # fills cannot regress to OPEN
        (S.UNKNOWN, S.REJECTED, 4, 4, 10),  # an order that traded was not rejected
        (S.OPEN, S.OPEN, 5, 4, 10),  # fills cannot shrink
    ],
)
def test_the_fill_quantities_must_fit_the_target_state(
    old: OrderState, new: OrderState, before: int, after: int, size: int
) -> None:
    with pytest.raises(ValueError):
        MACHINE.validate(old, new, before, after, size)
