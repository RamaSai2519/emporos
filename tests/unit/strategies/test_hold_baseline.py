"""hold_baseline_v1 buys once a day and does nothing else: the yardstick has to be that simple."""

from datetime import timedelta

from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from tests.unit.strategies.test_intraday_strategies import ALPHA, DAY1, Harness, flat_bars

DAY2 = DAY1 + timedelta(days=1)


def test_buys_once_on_the_first_bar_of_the_day_sized_to_the_position_budget() -> None:
    h = Harness("hold_baseline_v1", {})

    signals = h.feed(flat_bars(DAY1, 0, 6, "100"))

    (signal,) = signals
    assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
    assert signal.quantity == 500  # the budget, 50,000 // 100, before any risk rule trims it
    assert signal.ts == DAY1 + timedelta(minutes=5)  # on the close of the first bar


def test_buys_again_the_next_day_and_never_exits_itself() -> None:
    h = Harness("hold_baseline_v1", {})

    signals = h.feed([*flat_bars(DAY1, 0, 4), *flat_bars(DAY2, 0, 4)])

    assert [s.kind for s in signals] == [SignalKind.ENTRY, SignalKind.ENTRY]
    assert all(s.side is OrderSide.BUY for s in signals)  # the square-off is the session's job


def test_does_not_buy_while_it_already_holds_the_instrument() -> None:
    h = Harness("hold_baseline_v1", {})
    h.positions.set(ALPHA, 10, "100")

    assert h.feed(flat_bars(DAY1, 0, 4)) == []


def test_nothing_after_the_entry_cut_off() -> None:
    h = Harness("hold_baseline_v1", {}, no_entries_after="09:20")

    assert h.feed(flat_bars(DAY1, 0, 6)) == []  # the first bar closes at 09:20: not before it
