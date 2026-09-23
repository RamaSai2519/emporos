"""EM-185: the backtest event sink sees every signal, order, fill and trade, and a run with a
`NoSink` (the default) is byte-identical to a run without one — the sink only observes."""

from __future__ import annotations

from emporos.backtest.journal import NoSink, RecordingSink
from emporos.backtest.portfolio import TradeDirection
from emporos.domain.orders import OrderSide
from tests.support.backtest_engine import WORKED_DAY, bars, config, run


class TestTheRecordingSink:
    async def test_it_sees_every_signal_order_fill_and_trade(self) -> None:
        sink = RecordingSink()
        result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), sink=sink)

        assert [s.signal.side for s in sink.signals] == [OrderSide.BUY, OrderSide.SELL]
        assert all(not s.system for s in sink.signals)
        assert [o.request.side for o in sink.orders] == [OrderSide.BUY, OrderSide.SELL]
        assert [f.fill.side for f in sink.fills] == [OrderSide.BUY, OrderSide.SELL]
        (trade,) = sink.trades
        assert trade.trade.direction is TradeDirection.LONG
        assert trade.trade.quantity == 10
        # the sink's own trades match the result's, exactly
        assert tuple(t.trade for t in sink.trades) == result.trades

    async def test_sequences_are_one_based_and_in_order(self) -> None:
        sink = RecordingSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), sink=sink)

        assert [s.sequence for s in sink.signals] == [1, 2]
        assert [o.sequence for o in sink.orders] == [1, 2]
        assert [f.sequence for f in sink.fills] == [1, 2]

    async def test_a_gate_rejected_signal_is_still_recorded_as_a_signal_with_no_order(self) -> None:
        from emporos.backtest.pricing import GateRejection

        class RefuseAll:
            name = "refuse_all"

            def __init__(self, context=None) -> None:  # type: ignore[no-untyped-def]
                pass

            def review(self, signal):  # type: ignore[no-untyped-def]
                return GateRejection("no")

        sink = RecordingSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), gate=RefuseAll, sink=sink)

        assert len(sink.signals) == 2
        assert sink.orders == []
        assert sink.fills == []

    async def test_a_run_with_no_sink_matches_a_run_with_nosink_explicitly(self) -> None:
        implicit = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5))
        explicit = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), sink=NoSink())

        assert implicit.trades == explicit.trades
        assert implicit.metrics == explicit.metrics
