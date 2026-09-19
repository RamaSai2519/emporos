from __future__ import annotations

from datetime import timedelta

import pytest

from emporos.domain.candles import Timeframe
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.runner import (
    MAX_SIGNALS_PER_EVENT,
    RunnerState,
    StrategyRunner,
    WallClockSync,
)
from tests.support.strategies import (
    INSTRUMENT,
    OTHER_INSTRUMENT,
    RUN_ID,
    T0,
    ListFeed,
    ListSignalSink,
    RunnerRig,
    ScriptedStrategy,
    bar_at,
    make_config,
    make_signal,
    tick_at,
)


def _signal_per_bar(event: object, ctx: object) -> list[Signal]:
    return [make_signal(reason="each bar")]


async def test_the_lifecycle_runs_in_order_and_signals_reach_the_sink() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config, on_data=_signal_per_bar)
    rig = RunnerRig(strategy=strategy, config=config)

    report = await rig.runner.run(ListFeed([bar_at(minutes=0), bar_at(minutes=5)]))

    assert strategy.calls[0] == "initialize"
    assert strategy.calls[-3:] == ["on_session_end", "generate_signal", "on_shutdown"]
    assert len(rig.sink.signals) == 2 and report.signals_emitted == 2
    assert report.halted is False and rig.runner.state is RunnerState.STOPPED


async def test_generate_signal_is_called_until_the_strategy_has_nothing_more_to_say() -> None:
    config = make_config()
    strategy = ScriptedStrategy(
        config, on_data=lambda e, c: [make_signal(reason="a"), make_signal(reason="b")]
    )
    rig = RunnerRig(strategy=strategy, config=config)

    await rig.runner.run(ListFeed([bar_at()]))

    assert [s.reason for s in rig.sink.signals] == ["a", "b"]


async def test_a_bar_is_recorded_into_history_before_the_strategy_sees_it() -> None:
    config = make_config()
    seen: list[int] = []

    def on_data(event: object, ctx: object) -> list[Signal]:
        seen.append(len(rig.history.bars(INSTRUMENT, Timeframe.M5, 10)))
        return []

    rig = RunnerRig(strategy=ScriptedStrategy(config, on_data=on_data), config=config)

    await rig.runner.run(ListFeed([bar_at(minutes=0), bar_at(minutes=5)]))

    assert seen == [1, 2]  # bar t is already readable, and nothing later is


async def test_replay_moves_the_clock_to_each_bars_close_and_never_backwards() -> None:
    rig = RunnerRig()
    await rig.runner.start()

    await rig.runner.handle(bar_at(minutes=5))
    assert rig.clock.now() == T0 + timedelta(minutes=10)
    await rig.runner.handle(bar_at(minutes=0))  # an older bar cannot pull the clock back
    assert rig.clock.now() == T0 + timedelta(minutes=10)


async def test_ticks_are_delivered_but_not_recorded_as_bars() -> None:
    rig = RunnerRig()
    await rig.runner.start()

    await rig.runner.handle(tick_at(seconds=3))

    assert len(rig.strategy.events) == 1
    assert rig.history.bars(INSTRUMENT, Timeframe.M5, 10) == ()


async def test_a_bar_that_has_not_closed_is_never_delivered() -> None:
    """A wall-clock runner (no clock sync) that is handed a bar early must refuse it."""
    rig = RunnerRig()
    runner = StrategyRunner(
        rig.strategy, rig.context, rig.history, rig.sink, WallClockSync(), rig.alerts
    )
    await runner.start()

    await runner.handle(bar_at(minutes=0))  # the clock is still 09:15; the bar closes 09:20

    assert rig.strategy.events == []
    assert runner.report().unclosed_bars_skipped == 1 and not runner.halted
    assert rig.history.bars(INSTRUMENT, Timeframe.M5, 10) == ()


async def test_a_strategy_cannot_read_a_future_bar_through_the_context() -> None:
    """The feed runs ahead of the clock; the strategy still cannot see what has not closed."""
    config = make_config()
    visible: list[int] = []

    def on_data(event: object, ctx: object) -> list[Signal]:
        visible.append(len(rig.context.history.bars(INSTRUMENT, Timeframe.M5, 100)))
        return []

    rig = RunnerRig(strategy=ScriptedStrategy(config, on_data=on_data), config=config)
    for minute in (10, 15, 20):  # the feed has pre-loaded later bars into the store
        rig.history.record(bar_at(minutes=minute))
    await rig.runner.start()

    await rig.runner.handle(bar_at(minutes=0))  # clock -> 09:20

    assert visible == [
        1
    ]  # only the 09:15 bar; the pre-loaded 09:25/30/35 bars are held, unreadable


async def test_a_strategy_that_raises_is_halted_and_the_session_survives() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config, on_data=_signal_per_bar, fail_in="on_market_data")
    rig = RunnerRig(strategy=strategy, config=config)

    report = await rig.runner.run(ListFeed([bar_at(minutes=0), bar_at(minutes=5)]))

    assert report.halted and "on_market_data" in (report.halt_reason or "")
    assert rig.sink.signals == []
    (alert,) = rig.alerts.alerts
    assert alert[0] == "strategy_halted" and "boom" in alert[1]
    assert len(strategy.events) == 0  # the failing call was the first; nothing more was delivered
    assert strategy.calls.count("on_market_data") == 1
    assert strategy.calls[-1] == "on_shutdown"  # still shut down cleanly


@pytest.mark.parametrize(
    "where", ["initialize", "generate_signal", "on_order_update", "on_session_end"]
)
async def test_a_fault_in_any_handler_halts_the_strategy_not_the_session(where: str) -> None:
    config = make_config()
    rig = RunnerRig(strategy=ScriptedStrategy(config, fail_in=where), config=config)
    update = OrderUpdate(INSTRUMENT, OrderSide.BUY, OrderUpdateStatus.FILLED, 1, 1, T0)

    await rig.runner.start()
    await rig.runner.handle(bar_at())
    await rig.runner.handle_order_update(update)
    await rig.runner.end_session()
    await rig.runner.shutdown()

    assert rig.runner.halted
    assert len(rig.alerts.alerts) == 1  # halted once, not once per later event


async def test_a_halted_strategy_receives_nothing_further() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config, fail_in="on_market_data")
    rig = RunnerRig(strategy=strategy, config=config)
    await rig.runner.start()
    await rig.runner.handle(bar_at(minutes=0))

    await rig.runner.handle(bar_at(minutes=5))
    await rig.runner.handle_order_update(
        OrderUpdate(INSTRUMENT, OrderSide.BUY, OrderUpdateStatus.FILLED, 1, 1, T0)
    )

    assert strategy.calls.count("on_market_data") == 1 and strategy.updates == []


async def test_a_failing_on_shutdown_is_contained() -> None:
    config = make_config()
    rig = RunnerRig(strategy=ScriptedStrategy(config, fail_in="on_shutdown"), config=config)

    report = await rig.runner.run(ListFeed([bar_at()]))

    assert not report.halted and rig.runner.state is RunnerState.STOPPED


async def test_signals_valid_before_a_fault_are_still_forwarded() -> None:
    config = make_config()
    good = make_signal(reason="good")
    bad = make_signal(run_id="some-other-run", reason="bad")
    strategy = ScriptedStrategy(
        config, on_data=lambda e, c: [good, bad, make_signal(reason="lost")]
    )
    rig = RunnerRig(strategy=strategy, config=config)

    await rig.runner.run(ListFeed([bar_at()]))

    assert rig.sink.signals == [good]
    assert rig.runner.halted


@pytest.mark.parametrize(
    ("signal", "fragment"),
    [
        (make_signal(run_id="another-run"), "not " + RUN_ID),
        (make_signal(instrument_id=OTHER_INSTRUMENT), "not in the universe"),
    ],
)
async def test_a_signal_that_breaks_the_contract_halts_the_strategy(
    signal: Signal, fragment: str
) -> None:
    config = make_config()
    rig = RunnerRig(strategy=ScriptedStrategy(config, on_data=lambda e, c: [signal]), config=config)

    report = await rig.runner.run(ListFeed([bar_at()]))

    assert report.halted and fragment in (report.halt_reason or "")
    assert rig.sink.signals == []


async def test_something_that_is_not_a_signal_halts_the_strategy() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config, on_data=lambda e, c: [{"side": "BUY"}])  # type: ignore[list-item]
    rig = RunnerRig(strategy=strategy, config=config)

    report = await rig.runner.run(ListFeed([bar_at()]))

    assert report.halted and "not a Signal" in (report.halt_reason or "")


async def test_a_strategy_that_never_stops_signalling_is_halted() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config, forever=make_signal())
    rig = RunnerRig(strategy=strategy, config=config)

    report = await rig.runner.run(ListFeed([bar_at()]))

    assert report.halted and "signals at once" in (report.halt_reason or "")
    assert len(rig.sink.signals) == MAX_SIGNALS_PER_EVENT


async def test_a_failing_sink_is_infrastructure_and_propagates() -> None:
    """A signal that could not be recorded must not vanish: it is not the strategy's fault."""
    config = make_config()
    rig = RunnerRig(
        strategy=ScriptedStrategy(config, on_data=_signal_per_bar),
        sink=ListSignalSink(error=ConnectionError("mongo down")),
        config=config,
    )
    await rig.runner.start()

    with pytest.raises(ConnectionError):
        await rig.runner.handle(bar_at())

    assert not rig.runner.halted and rig.alerts.alerts == []


async def test_session_end_can_emit_final_exit_signals() -> None:
    config = make_config()
    strategy = ScriptedStrategy(config)
    strategy.queue_on_session_end = [make_signal(kind=SignalKind.EXIT, side=OrderSide.SELL)]
    rig = RunnerRig(strategy=strategy, config=config)

    await rig.runner.run(ListFeed([bar_at()]))

    assert [s.kind for s in rig.sink.signals] == [SignalKind.EXIT]


async def test_order_updates_reach_the_strategy_and_may_produce_signals() -> None:
    config = make_config()
    rig = RunnerRig(strategy=ScriptedStrategy(config), config=config)
    rig.strategy._outbox.append(make_signal(reason="after fill"))  # queued, emitted on next drain
    await rig.runner.start()
    update = OrderUpdate(INSTRUMENT, OrderSide.BUY, OrderUpdateStatus.FILLED, 5, 5, T0)

    await rig.runner.handle_order_update(update)

    assert rig.strategy.updates == [update]
    assert [s.reason for s in rig.sink.signals] == ["after fill"]


async def test_events_before_start_or_after_the_session_ends_are_ignored() -> None:
    rig = RunnerRig()

    await rig.runner.handle(bar_at())  # not started
    await rig.runner.start()
    await rig.runner.end_session()
    await rig.runner.handle(bar_at(minutes=5))  # session over
    await rig.runner.end_session()  # idempotent
    await rig.runner.shutdown()
    await rig.runner.shutdown()  # idempotent

    assert rig.strategy.events == []
    assert rig.strategy.calls.count("on_session_end") == 1
    assert rig.strategy.calls.count("on_shutdown") == 1


async def test_a_runner_cannot_be_started_twice() -> None:
    rig = RunnerRig()
    await rig.runner.start()
    with pytest.raises(RuntimeError, match="start"):
        await rig.runner.start()
