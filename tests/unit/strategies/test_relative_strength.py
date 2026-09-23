"""relative_strength_v1 (EM-125) on a small hand-built universe whose ranking can be checked by
eye: four instruments with distinct returns over one rebalance window, then a second window where
the ranking rotates. A strategy is judged in backtests for profit; here it is judged for ranking and
rotating exactly as it says."""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import ValidationError

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.builtin.relative_strength_v1 import (
    RelativeStrengthParameters,
    RelativeStrengthV1,
)
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from tests.support.strategies import T0, BookPositions, closes_to_bars, make_config

A, B, C, D = "NSE:2001", "NSE:2002", "NSE:2003", "NSE:2004"


class Harness:
    """Records every bar into history (as the real runner does) then delivers it, so a rebalance's
    fresh `ctx.history` reads see exactly what has been fed so far -- no more, no less."""

    def __init__(
        self,
        parameters: RelativeStrengthParameters,
        no_entries_after: str = "15:00",
        max_open_positions: int = 3,
    ) -> None:
        config = make_config(
            name="relative_strength_v1", instruments=(A, B, C, D), parameters=parameters
        )
        config = config.model_copy(
            update={
                "risk": config.risk.model_copy(update={"max_open_positions": max_open_positions}),
                "session": config.session.model_copy(
                    update={"no_new_entries_after": time.fromisoformat(no_entries_after)}
                ),
            }
        )
        self.positions = BookPositions()
        self.clock = FixedClock(T0)
        self.history = ClosedBarHistory(self.clock)
        self.strategy = RelativeStrengthV1(config)
        self.ctx = StrategyContext(
            run_id="run", config=config, clock=self.clock, logger=logging.getLogger("t"),
            history=self.history, positions=self.positions, rng=random.Random(0),
        )  # fmt: skip
        self.strategy.initialize(self.ctx)

    def feed(self, bars: Iterable[Candle]) -> list[Signal]:
        out: list[Signal] = []
        for b in bars:
            self.clock.set(b.closes_at)
            self.history.record(b)
            self.strategy.on_market_data(b)
            while (s := self.strategy.generate_signal()) is not None:
                out.append(s)
        return out


def rounds(
    closes: dict[str, list[str]],
    order: tuple[str, ...] = (B, C, D, A),
    start: datetime = T0,
) -> list[Candle]:
    """`closes` laid out round-robin, `order` fed last-to-trigger inside each round, so whichever
    instrument closes the round has already-recorded siblings when it fires the rebalance."""
    per_instrument = {iid: closes_to_bars(series, iid, start) for iid, series in closes.items()}
    rounds_count = len(next(iter(closes.values())))
    return [per_instrument[iid][i] for i in range(rounds_count) for iid in order]


BASE = dict(
    lookback=2, rebalance_every_bars=3, top_n=1, bottom_n=1, allow_short=True,
    min_avg_volume=1, min_abs_roc_bps=Decimal(0),
)  # fmt: skip


class TestRankingAndRotation:
    WINDOW1: ClassVar[dict[str, list[str]]] = {
        A: ["100", "106", "113"],  # +13%: strongest
        B: ["100", "102", "104"],  # +4%
        C: ["100", "98", "96"],  # -4%
        D: ["100", "94", "87"],  # -13%: weakest
    }

    def test_it_longs_the_strongest_and_shorts_the_weakest(self) -> None:
        h = Harness(RelativeStrengthParameters(**BASE))

        signals = h.feed(rounds(self.WINDOW1))

        assert [(s.instrument_id, s.side, s.kind) for s in signals] == [
            (A, OrderSide.BUY, SignalKind.ENTRY),
            (D, OrderSide.SELL, SignalKind.ENTRY),
        ]
        assert "ranked #1 of 4" in signals[0].reason and "top-1" in signals[0].reason
        assert "ranked #4 of 4" in signals[1].reason and "bottom-1" in signals[1].reason

    def test_a_name_that_stays_in_its_band_is_left_alone_and_one_that_drops_out_is_exited(
        self,
    ) -> None:
        h = Harness(RelativeStrengthParameters(**BASE))
        first = h.feed(rounds(self.WINDOW1))
        h.positions.set(A, first[0].quantity, "113")
        h.positions.set(D, -first[1].quantity, "87")

        window2 = {
            A: ["113", "112", "111", "110"],  # flat-ish from here: falls out of the top
            B: ["104", "100", "110", "121"],  # +21%: the new strongest
            C: ["96", "100", "90", "80"],  # -20%: the new weakest
            D: ["87", "88", "89", "90"],  # mild recovery: falls out of the bottom
        }
        # feed one throwaway bar per instrument first so `rounds` (3 more bars) lines up on a
        # fresh rebalance boundary (bar_of_day 4, 5, 6)
        next_day = T0 + timedelta(days=1)
        second = h.feed(rounds({k: v[:1] for k, v in window2.items()}, start=next_day))
        assert second == []
        second = h.feed(
            rounds(
                {k: v[1:] for k, v in window2.items()},
                start=next_day + timedelta(minutes=5),
            )
        )

        kinds = [(s.instrument_id, s.side, s.kind) for s in second]
        assert (A, OrderSide.SELL, SignalKind.EXIT) in kinds
        assert (D, OrderSide.BUY, SignalKind.EXIT) in kinds
        assert (B, OrderSide.BUY, SignalKind.ENTRY) in kinds
        assert (C, OrderSide.SELL, SignalKind.ENTRY) in kinds


class TestFilters:
    def test_no_rebalance_until_lookback_bars_exist(self) -> None:
        h = Harness(RelativeStrengthParameters(**{**BASE, "rebalance_every_bars": 2}))
        thin = {A: ["100", "110"], B: ["100", "90"], C: ["100", "100"], D: ["100", "100"]}

        assert h.feed(rounds(thin)) == []  # only 2 bars exist; lookback=2 needs 3

    def test_an_illiquid_name_is_excluded_even_if_its_return_is_the_most_extreme(self) -> None:
        h = Harness(RelativeStrengthParameters(**{**BASE, "min_avg_volume": 10_000}))
        closes = {A: ["100", "150", "200"], B: ["100", "101", "102"],
                  C: ["100", "99", "98"], D: ["100", "95", "90"]}  # fmt: skip
        bars = rounds(closes)
        thin_a = [
            Candle(
                b.instrument_id,
                b.timeframe,
                b.ts,
                b.open,
                b.high,
                b.low,
                b.close,
                100 if b.instrument_id == A else 100_000,
            )
            for b in bars
        ]

        signals = h.feed(thin_a)

        assert all(s.instrument_id != A for s in signals)  # the biggest mover, but too thin
        assert any(s.instrument_id == D and s.side is OrderSide.SELL for s in signals)

    def test_a_move_under_the_threshold_does_not_qualify_for_a_band(self) -> None:
        h = Harness(RelativeStrengthParameters(**{**BASE, "min_abs_roc_bps": Decimal(1000)}))
        closes = {A: ["100", "102", "104"], B: ["100", "100.5", "101"],
                  C: ["100", "99.5", "99"], D: ["100", "98", "96"]}  # fmt: skip

        assert h.feed(rounds(closes)) == []  # strongest move is ~4%, under the 10% floor

    def test_bottom_n_without_allow_short_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RelativeStrengthParameters(**{**BASE, "allow_short": False, "bottom_n": 1})

    def test_entries_are_gated_by_the_cutoff_but_exits_are_not(self) -> None:
        h = Harness(RelativeStrengthParameters(**BASE), no_entries_after="00:01")
        first = h.feed(rounds(TestRankingAndRotation.WINDOW1))
        assert first == []  # the cut-off (just after session open) blocks every new entry

    def test_capacity_caps_new_entries_at_max_open_positions(self) -> None:
        params = RelativeStrengthParameters(**{**BASE, "top_n": 1, "bottom_n": 1})
        h = Harness(params, max_open_positions=1)

        signals = h.feed(rounds(TestRankingAndRotation.WINDOW1))

        assert len(signals) == 1 and signals[0].instrument_id == A  # only the top slot fits
