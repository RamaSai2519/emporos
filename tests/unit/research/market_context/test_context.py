"""EM-239 L-D3: the as-of context, number by number, and the look-ahead guarantee."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.eventtrader.events import MarketEvent
from emporos.research.market_context.bars import BarSeriesCache, IntradayBars
from emporos.research.market_context.builder import (
    CONTEXT_KEYS,
    AsOfContextBuilder,
    SectorMap,
)

NAME, NIFTY, VIX, BANK = "NSE:1", "NSE:99926000", "NSE:99926017", "NSE:99926009"
SESSIONS = [date(2026, 3, 2) + timedelta(days=i) for i in range(30) if (i % 7) < 5]  # weekdays
FIRST_BAR = time(9, 15)


def day_bars(
    instrument: str, day: date, prices: Sequence[float], volume: int = 100
) -> list[Candle]:
    """One session of 5-minute bars: bar i opens at 09:15 + 5i and closes at prices[i]."""
    out: list[Candle] = []
    start = datetime.combine(day, FIRST_BAR, tzinfo=IST)
    previous = prices[0]
    for i, close in enumerate(prices):
        ts = (start + timedelta(minutes=5 * i)).astimezone(UTC)
        high, low = max(previous, close) + 0.5, min(previous, close) - 0.5
        out.append(
            Candle(
                instrument,
                Timeframe.M5,
                ts,
                Money.of(str(previous)),
                Money.of(str(high)),
                Money.of(str(low)),
                Money.of(str(close)),
                volume,
            )  # fmt: skip
        )
        previous = close
    return out


def series(instrument: str, base: float, drift: float, bars: int = 75) -> list[Candle]:
    """Every session ends `drift` above the previous one's end; prices rise 1 per bar in a day."""
    out: list[Candle] = []
    for n, day in enumerate(SESSIONS):
        opening = base + drift * n
        out += day_bars(instrument, day, [opening + i * 0.1 for i in range(bars)])
    return out


class Loader:
    def __init__(self, data: dict[str, list[Candle]]) -> None:
        self._data = data
        self.asked: list[tuple[str, date, date]] = []

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        self.asked.append((instrument_id, first, last))
        return self._data[instrument_id]


def builder(data: dict[str, list[Candle]]) -> AsOfContextBuilder:
    cache = BarSeriesCache(Loader(data), SESSIONS[0], SESSIONS[-1])
    return AsOfContextBuilder(
        cache, NIFTY, VIX, SectorMap({"ABB": (BANK, "Nifty Bank")})
    )  # fmt: skip


def event(at: datetime, instrument_id: str = NAME) -> MarketEvent:
    return MarketEvent(
        "NSE:1", instrument_id, "ABB", at, at, "filing", "Updates", "subject", "text"
    )  # fmt: skip


def world() -> dict[str, list[Candle]]:
    return {
        NAME: series(NAME, 100, 1.0),
        NIFTY: series(NIFTY, 20000, 20),
        VIX: series(VIX, 15, 0.0),
        BANK: series(BANK, 45000, 45),
    }


DAY = SESSIONS[12]  # a session with 12 before it
AT_1000 = datetime.combine(DAY, time(10, 0), tzinfo=IST)


class TestNumbers:
    def test_in_session_numbers_are_computed_from_the_bars_closed_so_far(self) -> None:
        ctx = builder(world()).context(event(AT_1000), AT_1000 + timedelta(minutes=2)).lines

        # bars starting 09:15..09:55 have closed by 10:02 (the 10:00 bar ends 10:05: not yet)
        # so 9 bars closed: the last at 09:55 is bar 8, close = opening + 0.8
        opening = 100 + 1.0 * 12
        assert ctx["session"] == "open"
        assert ctx["last_session"] == DAY.isoformat()
        assert ctx["last_price"] == pytest.approx(opening + 0.8)
        # the previous session's close: 75 bars, the last one is bar 74
        assert ctx["prev_close"] == pytest.approx(100 + 1.0 * 11 + 7.4)
        assert ctx["move_since_prev_close_pct"] == pytest.approx(
            ((opening + 0.8) / (100 + 11 + 7.4) - 1) * 100
        )

    def test_the_move_since_the_event_uses_the_price_at_the_event(self) -> None:
        at_event = datetime.combine(DAY, time(9, 30), tzinfo=IST)  # bars up to 09:25 closed
        ctx = builder(world()).context(event(at_event), AT_1000 + timedelta(minutes=2)).lines

        price_at_event = 112 + 0.2  # the bar starting 09:25 closes at 09:30: bar 2
        assert ctx["move_since_event_pct"] == pytest.approx(((112.8) / price_at_event - 1) * 100)

    def test_vwap_and_volume_against_the_norm(self) -> None:
        ctx = builder(world()).context(event(AT_1000), AT_1000 + timedelta(minutes=2)).lines

        assert -1.0 < float(ctx["vwap_distance_pct"]) < 1.0
        assert ctx["volume_vs_norm"] == pytest.approx(1.0)  # every session has the same volume

    def test_returns_and_volatility_look_back_over_completed_sessions_only(self) -> None:
        ctx = builder(world()).context(event(AT_1000), AT_1000 + timedelta(minutes=2)).lines

        five_ago = 100 + 1.0 * 7 + 7.4  # the close of the session 5 before today's
        assert ctx["ret_5d_pct"] == pytest.approx(((112.8) / five_ago - 1) * 100)
        assert float(ctx["vol_20d_pct"]) > 0

    def test_market_lines_carry_nifty_the_sector_and_the_vix(self) -> None:
        ctx = builder(world()).context(event(AT_1000), AT_1000 + timedelta(minutes=2)).lines

        assert "nifty_move_since_prev_close_pct" in ctx and "nifty_ret_5d_pct" in ctx
        assert ctx["sector_index"] == "Nifty Bank"
        assert "sector_move_since_prev_close_pct" in ctx
        assert ctx["india_vix"] == pytest.approx(15 + 0.8)
        assert "india_vix_change_pct" in ctx

    def test_every_key_is_a_documented_one(self) -> None:
        ctx = builder(world()).context(event(AT_1000), AT_1000 + timedelta(minutes=2)).lines

        assert set(ctx) <= set(CONTEXT_KEYS)


class TestSessionStates:
    def test_after_the_close_the_day_is_complete(self) -> None:
        at = datetime.combine(DAY, time(17, 0), tzinfo=IST)

        ctx = builder(world()).context(event(at), at + timedelta(minutes=2)).lines

        assert ctx["session"] == "after_close"
        assert ctx["last_price"] == pytest.approx(112 + 7.4)

    def test_before_the_first_bar_closes_the_last_session_is_what_is_known(self) -> None:
        at = datetime.combine(DAY, time(8, 0), tzinfo=IST)

        ctx = builder(world()).context(event(at), at + timedelta(minutes=2)).lines

        assert ctx["session"] == "closed"
        assert ctx["last_session"] == SESSIONS[11].isoformat()
        assert "move_since_prev_close_pct" not in ctx
        assert "vwap_distance_pct" not in ctx
        assert ctx["last_price"] == pytest.approx(111 + 7.4)

    def test_a_weekend_event_sees_friday(self) -> None:
        saturday = datetime.combine(date(2026, 3, 7), time(11, 0), tzinfo=IST)

        ctx = builder(world()).context(event(saturday), saturday + timedelta(minutes=2)).lines

        assert ctx["session"] == "closed"
        assert ctx["last_session"] == date(2026, 3, 6).isoformat()

    def test_a_name_without_an_instrument_id_gets_the_market_lines_only(self) -> None:
        ctx = builder(world()).context(event(AT_1000, ""), AT_1000 + timedelta(minutes=2)).lines

        assert "last_price" not in ctx
        assert "india_vix" in ctx and "nifty_ret_5d_pct" in ctx

    def test_too_little_history_leaves_the_long_numbers_out(self) -> None:
        early = datetime.combine(SESSIONS[2], time(10, 0), tzinfo=IST)

        ctx = builder(world()).context(event(early), early + timedelta(minutes=2)).lines

        assert "ret_20d_pct" not in ctx and "vol_20d_pct" not in ctx
        assert "volume_vs_norm" not in ctx  # needs five sessions


class TestNoLookAhead:
    """§12.2: a bar that closes after the decision time cannot change the answer."""

    @pytest.mark.parametrize("hour,minute", [(9, 40), (10, 0), (13, 5), (15, 26), (17, 0)])
    def test_absurd_later_bars_change_nothing(self, hour: int, minute: int) -> None:
        decision = datetime.combine(DAY, time(hour, minute), tzinfo=IST)
        honest = world()
        tampered = world()
        for instrument, bars in tampered.items():
            tampered[instrument] = [
                _absurd(b) if b.ts.astimezone(IST) + timedelta(minutes=5) > decision else b
                for b in bars
            ]  # every bar not closed by the decision time (and every later session) is nonsense

        one = builder(honest).context(event(decision - timedelta(minutes=5)), decision).lines
        two = builder(tampered).context(event(decision - timedelta(minutes=5)), decision).lines

        assert one == two
        assert one  # and it is not trivially empty

    def test_a_bar_that_closes_exactly_at_the_decision_time_is_included_and_the_next_is_not(
        self,
    ) -> None:
        decision = datetime.combine(DAY, time(10, 5), tzinfo=IST)  # the 10:00 bar closes now

        ctx = builder(world()).context(event(decision - timedelta(minutes=10)), decision).lines

        assert ctx["last_price"] == pytest.approx(112 + 0.9)  # the 10:00 bar (index 9)


def _absurd(bar: Candle) -> Candle:
    huge = Money.of("999999")
    return Candle(
        bar.instrument_id, bar.timeframe, bar.ts, huge, huge, Money.of("0.01"), huge, 10**9
    )  # fmt: skip


class TestIntradayBars:
    def test_bars_are_deduplicated_and_ordered(self) -> None:
        bars = day_bars(NAME, DAY, [1.0, 2.0, 3.0])

        series_ = IntradayBars([*reversed(bars), bars[0]])

        assert len(series_) == 3
        assert series_.completed(datetime.combine(DAY, time(9, 25), tzinfo=IST)) == 2

    def test_the_cache_loads_once_per_instrument_and_evicts_the_oldest(self) -> None:
        loader = Loader({"a": day_bars("a", DAY, [1.0]), "b": day_bars("b", DAY, [1.0])})
        cache = BarSeriesCache(loader, DAY, DAY, capacity=1)

        cache.bars("a")
        cache.bars("a")
        cache.bars("b")
        cache.bars("a")

        assert [i for i, _, _ in loader.asked] == ["a", "b", "a"]
