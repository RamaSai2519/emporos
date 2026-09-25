"""The 5-minute-candle market: sessions, daily aggregates, as-of closes, adjustment, the window."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.eventtrader.replay.vaulted_market import (
    NoAdjustment,
    ThreadedBarLoader,
    VaultedMarket,
    session_calendar,
)
from tests.unit.eventtrader.replay.fakes import MON, THU, TUE, WED, at, flat_day, replace_bar

D = Decimal
X = "NSE:2885"


class DictLoader:
    def __init__(self, bars: dict[str, list[Candle]]) -> None:
        self._bars = bars
        self.calls: list[tuple[str, date, date]] = []

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        self.calls.append((instrument_id, first, last))
        return [
            b
            for b in self._bars.get(instrument_id, [])
            if first <= b.ts.astimezone(at(MON, 0).tzinfo).date() <= last
        ]


class Halver:
    """A 2:1 split shown as a multiplier of 1/2 on everything before the given day."""

    def __init__(self, before: date) -> None:
        self._before = before

    def adjust_bars(self, instrument_id: str, raw: Sequence[Candle]) -> Sequence[Candle]:
        half = D("0.5")
        return [
            Candle(
                b.instrument_id, b.timeframe, b.ts, Money(b.open.amount * half),
                Money(b.high.amount * half), Money(b.low.amount * half),
                Money(b.close.amount * half), b.volume * 2,
            )
            if b.ts.astimezone(at(MON, 0).tzinfo).date() < self._before
            else b
            for b in raw
        ]  # fmt: skip


def loader() -> DictLoader:
    days = {MON: 100, TUE: 102, WED: 104, THU: 106}
    bars: dict[str, list[Candle]] = {X: [], "NSE:1": []}
    for d, price in days.items():
        day = flat_day(X, d, price)
        if d == TUE:
            day = replace_bar(day, 10, 0, o=102, h=110, low=95, c=103)
        bars[X] += day
    for d in (MON, TUE, WED, THU):
        bars["NSE:1"] += flat_day("NSE:1", d, 50)
    return DictLoader(bars)


def market(
    ld: DictLoader | None = None, last: date = THU, adjuster: object = None
) -> VaultedMarket:
    ld = ld or loader()
    sessions = session_calendar(ld, "NSE:1", MON, THU)
    return VaultedMarket(ld, adjuster or NoAdjustment(), sessions, MON, last)  # type: ignore[arg-type]


def test_the_sessions_are_the_days_the_reference_printed_bars() -> None:
    m = market()

    assert [m.is_session(d) for d in (MON, TUE, date(2024, 3, 9))] == [True, True, False]
    assert m.next_session(MON) == TUE and m.next_session(THU) is None


def test_daily_bars_aggregate_the_five_minute_bars_and_count_sessions_after_the_day() -> None:
    m = market()

    [tue, wed] = m.daily_bars(X, MON, 2)
    assert (tue.day, tue.open, tue.high, tue.low, tue.close) == (TUE, D(102), D(110), D(95), D(102))
    assert tue.volume == 75 * 100_000 and wed.day == WED
    assert [b.day for b in m.daily_bars(X, WED, 5)] == [THU]  # fewer at the end of the data


def test_the_last_close_is_the_last_bar_completed_at_or_before_the_moment() -> None:
    m = market()

    assert m.last_close(X, at(TUE, 9, 15)) == D(100)  # the first bar of Tuesday is not done yet
    assert m.last_close(X, at(TUE, 9, 20)) == D(102)
    assert m.last_close(X, at(MON, 9, 10)) is None


def test_the_window_ends_where_it_is_told_to() -> None:
    m = market(last=TUE)

    assert m.five_minute_bars(X, WED) == () and m.daily_bars(X, TUE, 3) == []
    assert m.next_session(TUE) is None


def test_a_name_is_loaded_once_over_the_whole_window() -> None:
    ld = loader()
    m = market(ld)
    m.five_minute_bars(X, MON)
    m.daily_bars(X, MON, 1)
    m.last_close(X, at(TUE, 12))

    assert [c for c in ld.calls if c[0] == X] == [(X, MON, THU)]


def test_bars_are_adjusted_before_anything_reads_them() -> None:
    m = market(adjuster=Halver(WED))

    assert m.last_close(X, at(MON, 9, 20)) == D(50)
    assert m.five_minute_bars(X, MON)[0].volume == 200_000
    assert m.daily_bars(X, TUE, 1)[0].close == D(104)  # Wednesday is on the new basis


def test_a_threaded_loader_returns_what_the_inner_loader_does() -> None:
    ld = loader()

    assert ThreadedBarLoader(ld).load(X, MON, MON) == ld.load(X, MON, MON)


def test_an_adjusted_loader_hands_out_bars_on_the_adjusted_basis() -> None:
    from emporos.eventtrader.replay.vaulted_market import AdjustedBarLoader

    adjusted = AdjustedBarLoader(loader(), Halver(WED))

    assert adjusted.load(X, MON, MON)[0].close.amount == D(50)
    assert adjusted.load(X, WED, WED)[0].close.amount == D(104)
