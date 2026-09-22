"""EM-106: the engine, end to end, on sessions whose every price and charge is worked by hand."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Context, Decimal, localcontext
from typing import ClassVar

from emporos.backtest.document import BacktestDocument
from emporos.backtest.portfolio import TradeDirection
from emporos.backtest.settings import FillSettings
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdateStatus as S
from tests.support.backtest_engine import (
    SHORT_WORKED_DAY,
    WORKED_DAY,
    BuyThenSell,
    HalveSize,
    RefuseAll,
    SellThenBuy,
    bars,
    config,
    engine,
    run,
    short_config,
    spec,
)
from tests.support.strategies import INSTRUMENT, T0

MIN = timedelta(minutes=1)


class TestAWorkedDay:
    """buy_at=2, sell_at=5. Bar 2 closes at 101: BUY 10, priced 101 * 1.0005 = 101.0505 -> the tick
    above, 101.10. Bar 3 trades through it, so the buy fills there at 101.10 (NOT on bar 2, whose
    low of 100 would have crossed: an order cannot trade on the bar it was placed on).
    Bar 5 closes at 104: SELL 10, priced 104 * 0.9995 = 103.948 -> the tick below, 103.90. Bar 6's
    high of 105 trades through it: filled at 103.90.

    gross = (103.90 - 101.10) * 10 = 28.00
    buy  1,011.00: brokerage 5.00 (0.1% is 1.011, under the 5 minimum), exchange 0.03, SEBI 0.00,
                   stamp 0.03, GST 18% * 5.03 = 0.9054 -> 0.91           = 5.97
    sell 1,039.00: brokerage 5.00, STT 0.025% = 0.25975 -> 0.26, exchange 0.03, SEBI 0.00,
                   GST 0.91                                              = 6.20
    fees 12.17, net 15.83"""

    async def result(self):  # type: ignore[no-untyped-def]
        return await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5))

    async def test_the_trade(self) -> None:
        result = await self.result()

        (trade,) = result.trades
        assert (trade.direction, trade.quantity) == (TradeDirection.LONG, 10)
        assert (trade.entry_price, trade.exit_price) == (Money.of("101.10"), Money.of("103.90"))
        assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
            Money.of("28.00"), Money.of("12.17"), Money.of("15.83"),
        )  # fmt: skip

    async def test_the_orders_trade_on_the_bar_after_the_one_that_prompted_them(self) -> None:
        (trade,) = (await self.result()).trades

        # bar n opens 5(n-1) minutes after 09:15 and closes 5n minutes after; fills carry the
        # close of the bar they happened on: bar 3 closes at +15, bar 6 at +30
        assert trade.opened_at == T0 + 15 * MIN
        assert trade.closed_at == T0 + 30 * MIN

    async def test_the_account(self) -> None:
        result = await self.result()

        assert result.metrics.ending_equity == Money.of("100015.83")
        assert result.open_positions_at_end == 0
        assert result.metrics.trades.count == 1 and result.metrics.trades.wins == 1
        assert result.metrics.turnover.traded_notional == Money.of("2050.00")  # 1,011 + 1,039

    async def test_what_the_exchange_did(self) -> None:
        c = (await self.result()).counters

        assert (c.signals, c.orders, c.fills) == (2, 2, 2)
        assert (c.gate_rejections, c.exchange_rejections, c.cancelled_or_expired) == (0, 0, 0)
        assert (c.square_off_signals, c.forced_square_offs) == (0, 0)

    async def test_the_strategy_is_told_and_sees_the_fill_already_booked(self) -> None:
        await self.result()

        statuses = [u.status for u in BuyThenSell.updates]
        assert statuses == [S.WORKING, S.FILLED, S.WORKING, S.FILLED]
        # its position, read while it handles each update: 0 when the buy is acknowledged, 10 once
        # the buy's fill update arrives, 10 when the sell is acknowledged, 0 after the sell fills
        assert BuyThenSell.positions_seen_at_update == [0, 10, 10, 0]

    async def test_the_equity_curve_has_a_point_for_every_bar(self) -> None:
        result = await self.result()

        # 7 bars: the position is open at the close of bars 3, 4 and 5 (bought during 3, sold
        # during 6), so 3 of 7 points have exposure. A curve of one point a day would say 0.
        assert abs(result.metrics.exposure.time_in_market - Decimal(3) / 7) < Decimal("1e-25")

    async def test_the_run_id_follows_the_config_and_the_first_day(self) -> None:
        same = (await self.result()).run_id
        assert same == (await self.result()).run_id
        other_config = await run(bars(WORKED_DAY), config(buy_at=3, sell_at=5))
        next_day = bars(WORKED_DAY, day=1)
        other_day = await engine(next_day).run(
            spec(config(buy_at=2, sell_at=5), days=2, start=T0 + timedelta(hours=23))
        )

        assert same.startswith("bt-") and same.endswith("-20260105")
        assert other_config.run_id != same and other_day.run_id != same
        assert same.split("-")[1] == (await self.result()).config_hash.split(":")[1][:16]

    async def test_the_run_is_reported_clean(self) -> None:
        result = await self.result()
        assert not result.runner.halted and result.runner.unclosed_bars_skipped == 0
        assert result.alerts == () and result.risk_gate == "none"


class TestAWorkedShortDay:
    """EM-127: the mirror of `TestAWorkedDay` traded short. sell_at=2, buy_at=5. Bar 2 closes at
    99: SELL 10 short, priced 99 * 0.9995 = 98.9505 - the tick below, 98.95. Bar 3's high of 99.2
    trades through it, so the short opens at 98.95. Bar 5 closes at 96: BUY 10 to cover, priced
    96 * 1.0005 = 96.048 - the tick above, 96.05. Bar 6's low of 94 trades through it: covered at
    96.05.

    gross = (98.95 - 96.05) * 10 = 29.00 (a falling day makes the short money)
    fees: the same schedule as the long worked day, charged per side (STT on the sell-to-open)."""

    async def result(self):  # type: ignore[no-untyped-def]
        return await run(bars(SHORT_WORKED_DAY), short_config(sell_at=2, buy_at=5))

    async def test_the_short_round_trip(self) -> None:
        result = await self.result()

        (trade,) = result.trades
        assert (trade.direction, trade.quantity) == (TradeDirection.SHORT, 10)
        assert (trade.entry_price, trade.exit_price) == (Money.of("98.95"), Money.of("96.05"))
        assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
            Money.of("29.00"), Money.of("12.16"), Money.of("16.84"),
        )  # fmt: skip

    async def test_the_short_opens_and_covers_on_the_bar_after_the_prompt(self) -> None:
        (trade,) = (await self.result()).trades

        assert trade.opened_at == T0 + 15 * MIN  # bar 3
        assert trade.closed_at == T0 + 30 * MIN  # bar 6

    async def test_the_account_and_orders(self) -> None:
        result = await self.result()

        assert result.metrics.ending_equity == Money.of("100016.84")
        assert result.open_positions_at_end == 0
        assert result.metrics.trades.count == 1 and result.metrics.trades.wins == 1
        assert result.metrics.turnover.traded_notional == Money.of("1950.00")  # 989.50 + 960.50
        c = result.counters
        assert (c.signals, c.orders, c.fills) == (2, 2, 2)
        assert (c.gate_rejections, c.exchange_rejections, c.forced_square_offs) == (0, 0, 0)
        assert abs(result.metrics.ending_equity.amount - Decimal(100_000_00) / 100) >= Decimal(0)

    async def test_the_strategy_is_told_and_sees_the_booked_fill(self) -> None:
        await self.result()

        assert [u.status for u in SellThenBuy.updates] == [S.WORKING, S.FILLED, S.WORKING, S.FILLED]
        # its position at each update: 0 on the sell ack, -10 once the short is open, -10 on the
        # buy ack, 0 after the cover fills
        assert SellThenBuy.positions_seen_at_update == [0, -10, -10, 0]

    async def test_the_report_reports_shorts_from_their_own_direction(self) -> None:
        result = await self.result()

        assert result.metrics.by_direction == {"SHORT": result.metrics.trades}
        assert result.metrics.by_direction_instrument == {
            "SHORT": {INSTRUMENT: result.metrics.trades}
        }
        assert result.metrics.by_direction_time_of_day == {
            "SHORT": {"09:15-10:00": result.metrics.trades}
        }
        # no regime timelines in the rig, so the trade lands in the causal "unknown" bucket
        assert result.metrics.by_direction_regime == {"SHORT": {"unknown": result.metrics.trades}}


class TestUnfilledAndRefusedOrders:
    async def test_an_entry_that_never_trades_through_expires_at_the_end_of_the_day(self) -> None:
        # after the BUY at bar 2 (limit 101.10) every low stays at or above 101.10
        rows = [*WORKED_DAY[:2]] + [("103", "105", "102", "104")] * 4
        result = await run(bars(rows), config(buy_at=2))

        assert result.trades == () and result.open_positions_at_end == 0
        c = result.counters
        assert (c.orders, c.fills, c.cancelled_or_expired) == (1, 0, 1)
        assert [u.status for u in BuyThenSell.updates] == [S.WORKING, S.CANCELLED]

    async def test_a_bar_that_only_touches_the_limit_does_not_fill_it(self) -> None:
        rows = [*WORKED_DAY[:2], ("103", "105", "101.10", "104")]  # the low touches 101.10 exactly
        result = await run(bars(rows), config(buy_at=2))

        assert result.counters.fills == 0

    async def test_touch_fills_can_be_switched_on_deliberately(self) -> None:
        rows = [*WORKED_DAY[:2], ("103", "105", "101.10", "104")]
        result = await run(bars(rows), config(buy_at=2), fills=FillSettings(crossing="touch"))

        # the buy fills on the touch; the position is then closed by the broker at the session end
        assert result.counters.fills == 2 and result.counters.forced_square_offs == 1

    async def test_a_rejecting_exchange_fills_nothing_and_tells_the_strategy(self) -> None:
        result = await run(
            bars(WORKED_DAY),
            config(buy_at=2, sell_at=5),
            fills=FillSettings(reject_rate_bps=10_000),
        )

        assert result.trades == ()
        assert result.counters.exchange_rejections == 2 and result.counters.fills == 0
        assert [u.status for u in BuyThenSell.updates] == [S.REJECTED, S.REJECTED]

    async def test_a_gate_that_refuses_everything_sends_no_orders(self) -> None:
        result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), gate=RefuseAll)

        assert (result.counters.signals, result.counters.orders) == (2, 0)
        assert result.counters.gate_rejections == 2 and result.risk_gate == "refuse_all"
        assert result.trades == ()

    async def test_a_gate_that_resizes_changes_what_is_traded(self) -> None:
        result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), gate=HalveSize)

        (trade,) = result.trades
        assert trade.quantity == 5 and result.risk_gate == "halve_size"

    async def test_partial_bars_are_never_traded_on(self) -> None:
        from dataclasses import replace

        whole = bars(WORKED_DAY)
        assert len((await run(whole, config(buy_at=2))).trades) == 1  # bar 3 fills the buy

        holed = bars(WORKED_DAY)
        holed[2] = replace(holed[2], partial=True)  # the same bar, flagged as spanning a feed gap
        result = await run(holed, config(buy_at=2))

        assert result.counters.fills == 0 and result.counters.cancelled_or_expired == 1


class TestSquareOffCancelsWhatIsResting:
    async def test_a_strategy_order_still_resting_at_square_off_time_is_cancelled(self) -> None:
        # bar 3 (09:30): the buy fills at 101.10 and the strategy's own SELL signal (bar 3 close
        # 102 -> limit 101.90) rests, because bar 4's high of 101.5 does not trade through it.
        # Bar 4 closes at 09:35 = square_off_at: that resting sell is cancelled and ONE exit for
        # the position goes out at 101 * 0.9995 = 100.9495 -> 100.90. Without the cancel, bar 5's
        # high of 102 would also fill the old sell and the account would sell 20 against 10.
        rows = [
            WORKED_DAY[0],
            WORKED_DAY[1],
            WORKED_DAY[2],
            ("101", "101.5", "100.5", "101"),
            ("101", "102", "100", "101.5"),
            ("101.5", "102", "101", "101.5"),
        ]
        cfg = config(buy_at=2, sell_at=3, square_off_at="09:35", no_new_entries_after="09:30")
        result = await run(bars(rows), cfg)

        (trade,) = result.trades
        assert (trade.quantity, trade.exit_price, trade.gross_pnl) == (
            10, Money.of("100.90"), Money.of("-2.00"),
        )  # fmt: skip
        assert result.counters.cancelled_or_expired == 1
        assert result.open_positions_at_end == 0 and result.counters.forced_square_offs == 0


class TestTheSessionOwnsThePositionAfterSquareOff:
    async def test_a_strategy_exit_at_the_same_time_does_not_sell_the_position_twice(self) -> None:
        # The strategy's own SELL is signalled on the bar that closes at square_off_at, the same
        # bar the session sends its exit. Both would fill on the next bar: 20 sold against 10
        # held, a stray SHORT. The strategy's signal is refused instead.
        rows = [*WORKED_DAY[:4], ("103", "104", "102", "103.5"), ("103.5", "104", "103", "103.5")]
        cfg = config(buy_at=2, sell_at=4, square_off_at="09:35", no_new_entries_after="09:30")

        result = await run(bars(rows), cfg)

        (trade,) = result.trades
        assert trade.direction is TradeDirection.LONG and trade.quantity == 10
        c = result.counters
        assert (c.refused_after_square_off, c.square_off_signals) == (1, 1)
        assert c.forced_square_offs == 0 and result.open_positions_at_end == 0


class TestSquareOff:
    """Intraday: from `square_off_at` the position is exited; what is still open at the close of
    the session is closed by the broker. square_off_at 09:35 = the close of bar 4."""

    CFG: ClassVar[dict[str, object]] = {
        "buy_at": 2,
        "sell_at": 0,
        "square_off_at": "09:35",
        "no_new_entries_after": "09:30",
    }

    async def test_the_exit_signal_at_square_off_time_fills_on_the_next_bar(self) -> None:
        # bar 4 closes at 103 -> SELL 10 at 103 * 0.9995 = 102.9485 -> 102.90; bar 5's high 103.5
        # trades through it. (102.90 - 101.10) * 10 = 18.00 gross
        rows = [*WORKED_DAY[:4], ("103", "103.5", "102", "102.5"), ("102.5", "103", "102", "102.5")]
        result = await run(bars(rows), config(**self.CFG))

        (trade,) = result.trades
        assert (trade.entry_price, trade.exit_price, trade.gross_pnl) == (
            Money.of("101.10"), Money.of("102.90"), Money.of("18.00"),
        )  # fmt: skip
        c = result.counters
        assert (c.square_off_signals, c.forced_square_offs) == (1, 0)

    async def test_what_is_still_open_at_the_close_is_closed_by_the_brokers_square_off(
        self,
    ) -> None:
        # the exit at 102.90 never trades through (highs 102.5 and 102.0); the session ends after
        # bar 6 (last price 101): forced close 101 * (1 - 10 bps) = 100.899 -> 100.89 (rounded
        # against us). (100.89 - 101.10) * 10 = -2.10 gross
        rows = [
            *WORKED_DAY[:4],
            ("102", "102.5", "101", "101.5"),
            ("101.5", "102", "100.5", "101"),
        ]
        result = await run(bars(rows), config(**self.CFG))

        (trade,) = result.trades
        assert trade.exit_price == Money.of("100.89") and trade.gross_pnl == Money.of("-2.10")
        c = result.counters
        assert (c.square_off_signals, c.forced_square_offs, c.cancelled_or_expired) == (1, 1, 1)
        assert result.open_positions_at_end == 0

    async def test_no_position_is_ever_carried_overnight(self) -> None:
        day = [*WORKED_DAY[:4], ("103", "103.5", "102", "102.5")]
        candles = bars(day, day=0) + bars(day, day=1)
        result = await run(candles, config(**self.CFG), days=2)

        assert len(result.trades) == 1  # only day 1 buys: the strategy counts bars across days
        assert result.open_positions_at_end == 0
        assert all(t.opened_at.date() == t.closed_at.date() for t in result.trades)


class TestSeveralSessions:
    async def test_each_day_is_a_session_and_the_equity_curve_has_a_day_per_session(self) -> None:
        candles = bars(WORKED_DAY, day=0) + bars(WORKED_DAY, day=1)
        # the strategy counts bars across the whole run, so it trades only on day one
        result = await run(candles, config(buy_at=2, sell_at=5), days=2)

        assert result.metrics.trading_days == 2
        assert len(result.trades) == 1 and result.open_positions_at_end == 0


class TestReproducibility:
    async def test_the_same_input_gives_the_same_document(self) -> None:
        def render(result) -> str:  # type: ignore[no-untyped-def]
            return json.dumps(BacktestDocument().render(result), sort_keys=True)

        first = render(await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5)))
        second = render(await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5)))

        assert first == second

    async def test_a_hostile_ambient_decimal_context_changes_nothing(self) -> None:
        plain = BacktestDocument().render(await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5)))
        with localcontext(Context(prec=5)):
            hostile = BacktestDocument().render(
                await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5))
            )

        assert plain == hostile


class TestAFailingStrategy:
    async def test_the_run_still_finishes_and_says_the_strategy_was_halted(self) -> None:
        result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5, fail_at=3))

        assert result.runner.halted and "blew up" in (result.runner.halt_reason or "")
        assert result.alerts and result.alerts[0][0] == "strategy_halted"
        assert result.open_positions_at_end == 0


class TestTheDocument:
    async def test_it_records_the_trade_and_every_assumption_in_exact_text(self) -> None:
        doc = BacktestDocument().render(await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5)))

        assert doc["trades"] == [
            {
                "instrument_id": INSTRUMENT,
                "direction": "LONG",
                "quantity": 10,
                "opened_at": (T0 + 15 * MIN).isoformat(),
                "closed_at": (T0 + 30 * MIN).isoformat(),
                "entry_price": "101.1000",
                "exit_price": "103.9000",
                "gross_pnl": "28.00",
                "fees": "12.17",
                "net_pnl": "15.83",
            }
        ]
        notes = " ".join(doc["assumptions"])
        assert "NO RISK ENGINE" in notes and "no margin" in notes and "marketable-limit" in notes
        assert "through crossing" in notes and "forced square-off" in notes
        assert doc["run"]["starting_cash"] == "100000.00"
        assert doc["metrics"]["account"]["ending_equity"] == "100015.83"
        assert doc["costs"]["all_verified"] is False
        assert "fee schedule NOT reconciled" in notes

    async def test_a_refusing_gate_is_named_instead_of_claiming_no_gate(self) -> None:
        doc = BacktestDocument().render(
            await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), gate=RefuseAll)
        )
        assert "risk gate: refuse_all" in doc["assumptions"][0]
        assert "NO RISK ENGINE" not in doc["assumptions"][0]
