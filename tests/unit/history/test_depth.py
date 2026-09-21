"""EM-132: the probe finds a broker's per-request span and history depth by asking, against a fake
that truncates silently (like the real one) and holds data from a known day."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from emporos.broker.errors import BrokerTransportError
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.history.depth import HistoryDepthProbe, ProbeInconclusiveError

INSTRUMENT = Instrument(Exchange.NSE, "3045", "SBIN-EQ", "SBI", 1, Money(Decimal("0.05")))
TODAY = date(2026, 9, 18)


class FakeBroker:
    """Serves one bar per weekday from `oldest`; a request longer than `limit_days` is silently cut
    to its LATEST `limit_days` (as Angel One does)."""

    def __init__(self, limit_days: int, oldest: date, fail_on: date | None = None) -> None:
        self.limit_days = limit_days
        self.oldest = oldest
        self.fail_on = fail_on
        self.requests: list[tuple[datetime, datetime]] = []

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.requests.append((start, end))
        if self.fail_on is not None and start.astimezone(IST).date() == self.fail_on:
            raise BrokerTransportError("connection reset")
        if end - start > timedelta(days=self.limit_days):
            start = end - timedelta(days=self.limit_days)
        bars: list[Candle] = []
        day = start.astimezone(IST).date()
        while day <= end.astimezone(IST).date():
            moment = datetime.combine(day, time(9, 15), tzinfo=IST)
            if day.weekday() < 5 and day >= self.oldest and start <= moment < end:
                bars.append(self._bar(timeframe, moment.astimezone(UTC)))
            day += timedelta(days=1)
        return bars

    @staticmethod
    def _bar(timeframe: Timeframe, ts: datetime) -> Candle:
        price = Money(Decimal("100"))
        return Candle(INSTRUMENT.instrument_id, timeframe, ts, price, price, price, price, 1)


def probe(broker: FakeBroker) -> HistoryDepthProbe:
    return HistoryDepthProbe(broker, INSTRUMENT)


async def test_the_span_limit_is_the_longest_request_that_came_back_whole() -> None:
    broker = FakeBroker(limit_days=100, oldest=date(2015, 1, 1))

    found = await probe(broker).span_limit(Timeframe.M5, TODAY)

    assert (found.largest_whole_days, found.first_truncated_days) == (100, 120)
    assert found.truncated_to_days is not None and found.truncated_to_days <= 100


async def test_a_thirty_day_broker_is_found_too() -> None:
    found = await probe(FakeBroker(30, date(2015, 1, 1))).span_limit(Timeframe.M1, TODAY)

    assert (found.largest_whole_days, found.first_truncated_days) == (30, 45)  # a bracket


async def test_a_broker_with_no_limit_on_the_ladder_reports_no_truncation() -> None:
    found = await probe(FakeBroker(5000, date(2015, 1, 1))).span_limit(Timeframe.D1, TODAY)

    assert found.largest_whole_days == 730 and found.first_truncated_days is None


async def test_the_depth_is_found_to_the_week_and_reported_as_the_oldest_bar() -> None:
    oldest = date(2021, 6, 15)  # a Tuesday
    found = await probe(FakeBroker(100, oldest)).depth(Timeframe.M5, TODAY)

    assert found.earliest_day is not None
    assert oldest <= found.earliest_day <= oldest + timedelta(days=7)
    assert found.bars_in_probe_week >= 1


async def test_a_short_history_is_bracketed_in_the_first_year() -> None:
    oldest = date(2026, 3, 2)
    found = await probe(FakeBroker(100, oldest)).depth(Timeframe.M1, TODAY)

    assert found.earliest_day is not None
    assert oldest <= found.earliest_day <= oldest + timedelta(days=7)


async def test_no_data_at_all_is_reported_as_no_depth() -> None:
    found = await probe(FakeBroker(100, date(2030, 1, 1))).depth(Timeframe.M5, TODAY)

    assert found.earliest_day is None and found.bars_in_probe_week == 0


async def test_a_report_gives_every_timeframe_its_own_finding_and_call_count() -> None:
    broker = FakeBroker(100, date(2023, 1, 2))

    report = await probe(broker).probe([Timeframe.M5, Timeframe.D1], TODAY)

    assert report.instrument == "NSE:3045" and report.as_of == TODAY
    assert [f.span.timeframe for f in report.findings] == [Timeframe.M5, Timeframe.D1]
    assert sum(f.calls for f in report.findings) == len(broker.requests)
    assert all(f.calls > 0 for f in report.findings)


async def test_a_transient_failure_stops_the_probe_instead_of_shortening_the_finding() -> None:
    year_back = TODAY.replace(year=TODAY.year - 2)
    broker = FakeBroker(100, date(2015, 1, 1), fail_on=year_back)

    with pytest.raises(ProbeInconclusiveError, match="connection reset"):
        await probe(broker).depth(Timeframe.M5, TODAY)
