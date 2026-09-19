from dataclasses import dataclass

from emporos.core.eventbus import EventBus


@dataclass
class FillApplied:
    order_id: str


@dataclass
class SignalGenerated:
    instrument_id: str


def test_publish_dispatches_only_to_subscribers_of_that_type() -> None:
    bus = EventBus()
    fills: list[FillApplied] = []
    signals: list[SignalGenerated] = []
    bus.subscribe(FillApplied, fills.append)
    bus.subscribe(SignalGenerated, signals.append)

    bus.publish(FillApplied(order_id="o1"))

    assert fills == [FillApplied(order_id="o1")]
    assert signals == []


def test_publish_is_synchronous_and_ordered() -> None:
    bus = EventBus()
    order: list[str] = []
    bus.subscribe(FillApplied, lambda e: order.append(f"first:{e.order_id}"))
    bus.subscribe(FillApplied, lambda e: order.append(f"second:{e.order_id}"))

    bus.publish(FillApplied(order_id="o1"))

    assert order == ["first:o1", "second:o1"]


def test_a_raising_handler_does_not_stop_other_handlers() -> None:
    bus = EventBus()
    calls: list[str] = []

    def bad_handler(_: FillApplied) -> None:
        raise RuntimeError("boom")

    bus.subscribe(FillApplied, bad_handler)
    bus.subscribe(FillApplied, lambda e: calls.append(e.order_id))

    bus.publish(FillApplied(order_id="o1"))  # must not raise

    assert calls == ["o1"]


def test_publish_with_no_subscribers_is_a_noop() -> None:
    bus = EventBus()
    bus.publish(FillApplied(order_id="o1"))  # must not raise


def test_unsubscribe_stops_future_dispatch() -> None:
    bus = EventBus()
    calls: list[str] = []
    handler = calls.append

    bus.subscribe(SignalGenerated, lambda e: handler(e.instrument_id))
    bus.subscribe(SignalGenerated, handler2 := (lambda e: calls.append(e.instrument_id)))
    bus.unsubscribe(SignalGenerated, handler2)
    bus.publish(SignalGenerated(instrument_id="NSE:INFY"))

    assert calls == ["NSE:INFY"]
