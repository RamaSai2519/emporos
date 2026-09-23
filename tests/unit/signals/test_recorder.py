from __future__ import annotations

from datetime import timedelta

from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.signals.recorder import SignalRecorder
from tests.support.strategies import INSTRUMENT, T0, InMemorySignalStore, make_signal


async def test_a_signal_is_persisted_with_everything_it_says() -> None:
    store = InMemorySignalStore()
    stop = Signal(
        strategy_run_id="run-1",
        instrument_id=INSTRUMENT,
        kind=SignalKind.EXIT,
        side=OrderSide.SELL,
        order_type=OrderType.STOPLOSS_LIMIT,
        quantity=7,
        limit_price=Money.of("98.5"),
        trigger_price=Money.of("99"),
        ts=T0,
        reason="protect the gain",
    )

    await SignalRecorder(store, IdGenerator()).submit(stop)

    (record,) = store.records
    assert (record.strategy_run_id, record.instrument_id, record.ts) == ("run-1", INSTRUMENT, T0)
    assert (record.kind, record.side, record.order_type) == (
        "EXIT",
        OrderSide.SELL,
        OrderType.STOPLOSS_LIMIT,
    )
    assert (record.price, record.trigger_price, record.quantity) == (
        Money.of("98.5"),
        Money.of("99"),
        7,
    )
    assert record.reason == "protect the gain" and record.ordertag is None  # execution links it


async def test_signals_are_sequenced_per_run_and_get_unique_ids() -> None:
    store = InMemorySignalStore()
    recorder = SignalRecorder(store, IdGenerator())

    for run_id in ("run-a", "run-a", "run-b", "run-a"):
        await recorder.submit(make_signal(run_id=run_id, ts=T0 + timedelta(seconds=0)))

    assert [(r.strategy_run_id, r.sequence) for r in store.records] == [
        ("run-a", 1),
        ("run-a", 2),
        ("run-b", 1),
        ("run-a", 3),
    ]
    assert len({r.id for r in store.records}) == 4


async def test_stamping_a_quote_adds_context_without_touching_the_signal() -> None:
    from emporos.signals.quotes import DecisionQuote

    store = InMemorySignalStore()
    recorder = SignalRecorder(store, IdGenerator())
    signal_id = await recorder.record(make_signal())
    before = store.records[0]

    await recorder.stamp_quote(signal_id, DecisionQuote(T0, "q", Money.of("100"), None, None))

    (after,) = store.records
    assert (after.quote_ltp, after.quote_bid, after.quote_source) == (Money.of("100"), None, "q")
    assert (
        after.model_copy(update={"quote_ltp": None, "quote_ts": None, "quote_source": None})
        == before
    )
