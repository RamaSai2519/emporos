"""A dictionary-backed market for the replay tests: hand-built 5-minute and daily bars."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.eventtrader.replay.market import DailyBar

D = Decimal
MON, TUE, WED, THU, FRI = (date(2024, 3, d) for d in (4, 5, 6, 7, 8))
SAT, NEXT_MON = date(2024, 3, 9), date(2024, 3, 11)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


def bar(
    instrument: str, day: date, hh: int, mm: int, o: object, h: object, low: object, c: object,
    volume: int = 100_000,
) -> Candle:  # fmt: skip
    ts = at(day, hh, mm).astimezone(UTC)
    return Candle(
        instrument, Timeframe.M5, ts, Money.of(str(o)), Money.of(str(h)), Money.of(str(low)),
        Money.of(str(c)), volume,
    )  # fmt: skip


def flat_day(
    instrument: str, day: date, price: object = 100, volume: int = 100_000
) -> list[Candle]:
    """75 flat bars at `price`."""
    start = at(day, 9, 15)
    return [
        bar(instrument, day, (start + timedelta(minutes=5 * i)).hour,
            (start + timedelta(minutes=5 * i)).minute, price, price, price, price, volume)
        for i in range(75)
    ]  # fmt: skip


def replace_bar(bars: list[Candle], hh: int, mm: int, **kw: object) -> list[Candle]:
    """`bars` with the bar STARTING at hh:mm replaced by one with the given o/h/low/c/volume."""
    out = []
    for b in bars:
        local = b.ts.astimezone(IST)
        if (local.hour, local.minute) == (hh, mm):
            values = {
                "o": b.open.amount, "h": b.high.amount, "low": b.low.amount, "c": b.close.amount,
                "volume": b.volume, **kw,
            }  # fmt: skip
            b = bar(
                b.instrument_id,
                local.date(),
                hh,
                mm,
                values["o"],
                values["h"],
                values["low"],
                values["c"],
                int(values["volume"]),
            )  # type: ignore[call-overload]
        out.append(b)
    return out


class FakeMarket:
    def __init__(self, sessions: list[date]) -> None:
        self.sessions = sorted(sessions)
        self.five: dict[tuple[str, date], list[Candle]] = {}
        self.daily: dict[str, dict[date, DailyBar]] = {}

    def put_day(self, instrument: str, day: date, bars: list[Candle]) -> None:
        self.five[(instrument, day)] = bars

    def put_daily(self, instrument: str, bar_: DailyBar) -> None:
        self.daily.setdefault(instrument, {})[bar_.day] = bar_

    def five_minute_bars(self, instrument_id: str, day: date) -> Sequence[Candle]:
        return self.five.get((instrument_id, day), [])

    def daily_bars(self, instrument_id: str, after: date, count: int) -> Sequence[DailyBar]:
        days = [d for d in self.sessions if d > after][:count]
        table = self.daily.get(instrument_id, {})
        return [table[d] for d in days if d in table]

    def next_session(self, day: date) -> date | None:
        return next((d for d in self.sessions if d > day), None)

    def is_session(self, day: date) -> bool:
        return day in self.sessions

    def last_close(self, instrument_id: str, at: datetime) -> Decimal | None:
        for day in sorted({d for (i, d) in self.five if i == instrument_id}, reverse=True):
            for b in reversed(self.five[(instrument_id, day)]):
                if b.closes_at <= at:
                    return b.close.amount
        return None


def daily(
    day: date, o: object, h: object, low: object, c: object, volume: int = 1_000_000
) -> DailyBar:
    return DailyBar(day, D(str(o)), D(str(h)), D(str(low)), D(str(c)), volume)
