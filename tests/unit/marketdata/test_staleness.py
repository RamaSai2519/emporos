"""EM-53: staleness is detected within one threshold window, blocks entries but never exits,
is queryable by the risk engine, and clears when a fresh tick arrives."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.core.clock import IST, FixedClock
from emporos.core.errors import ConfigurationError
from emporos.marketdata.staleness import (
    LiquidityTier,
    StalenessView,
    StalenessWatchdog,
    TieredThresholds,
)
from tests.support.fakes import RecordingAlertSink, make_tick

FRIDAY = date(2026, 9, 18)
SBIN, THIN = "NSE:3045", "NSE:999"


def ist(hour: int, minute: int, second: int = 0, day: date = FRIDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).astimezone(UTC)


class Listener:
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_stale(self, instrument_id: str, silent_for: timedelta) -> None:
        self.events.append(f"stale:{instrument_id}:{int(silent_for.total_seconds())}")

    def on_recovered(self, instrument_id: str) -> None:
        self.events.append(f"recovered:{instrument_id}")


class Rig:
    def __init__(self, at: datetime | None = None, watch: tuple[str, ...] = (SBIN,)) -> None:
        self.clock = FixedClock(at or ist(10, 0))
        self.alerts = RecordingAlertSink()
        self.listener = Listener()
        policy = TieredThresholds({THIN: LiquidityTier.LOW, SBIN: LiquidityTier.HIGH})
        self.dog = StalenessWatchdog(self.clock, policy, alerts=self.alerts)
        self.dog.add_listener(self.listener)
        self.dog.watch(watch)

    def tick(self, instrument_id: str = SBIN) -> None:
        self.dog.on_tick(make_tick(self.clock.now(), instrument_id=instrument_id))

    def wait(self, seconds: float) -> None:
        self.clock.advance(timedelta(seconds=seconds))


def test_an_instrument_goes_stale_only_after_its_threshold_is_exceeded() -> None:
    rig = Rig()
    rig.tick(SBIN)  # HIGH liquidity: 10s allowance

    rig.wait(10)
    assert not rig.dog.is_stale(SBIN)  # exactly at the threshold is still fresh
    rig.wait(0.001)
    assert rig.dog.is_stale(SBIN)


def test_staleness_is_detected_within_one_threshold_window_by_a_once_a_second_check() -> None:
    rig = Rig()
    rig.tick(SBIN)

    detected_after = None
    for second in range(1, 30):
        rig.wait(1)
        if rig.dog.check():
            detected_after = second
            break

    assert detected_after == 11  # threshold 10s + at most one check interval
    assert rig.dog.stale_instruments() == frozenset({SBIN})


def test_thresholds_are_liquidity_aware() -> None:
    rig = Rig(watch=(SBIN, THIN))
    rig.tick(SBIN)
    rig.tick(THIN)

    rig.wait(60)

    assert rig.dog.is_stale(SBIN) and not rig.dog.is_stale(THIN)  # the thin stock may be quiet
    rig.wait(61)
    assert rig.dog.is_stale(THIN)  # ...but not forever (120s)


def test_stale_data_blocks_entries_but_never_exits() -> None:
    rig = Rig()
    rig.tick(SBIN)
    rig.wait(60)

    assert rig.dog.is_stale(SBIN)
    assert rig.dog.permits_entry(SBIN) is False
    assert rig.dog.permits_exit(SBIN) is True  # a position must never be trapped by quiet data


def test_exits_are_permitted_in_every_state() -> None:
    rig = Rig()
    for _ in range(3):
        assert rig.dog.permits_exit(SBIN) and rig.dog.permits_exit("NSE:UNKNOWN")
        rig.wait(3600)


def test_the_risk_engine_can_query_it_through_the_narrow_view() -> None:
    rig = Rig()
    view: StalenessView = rig.dog
    rig.tick(SBIN)
    assert view.permits_entry(SBIN) and not view.is_stale(SBIN)
    rig.wait(60)
    assert not view.permits_entry(SBIN) and view.is_stale(SBIN)


def test_a_fresh_tick_clears_the_flag_immediately_and_notifies() -> None:
    rig = Rig()
    rig.tick(SBIN)
    rig.wait(60)
    rig.dog.check()
    assert rig.dog.is_stale(SBIN)

    rig.tick(SBIN)  # data resumes

    assert not rig.dog.is_stale(SBIN) and rig.dog.permits_entry(SBIN)
    assert rig.listener.events == ["stale:NSE:3045:60", "recovered:NSE:3045"]


def test_one_alert_per_transition_not_one_per_check() -> None:
    rig = Rig()
    rig.tick(SBIN)
    rig.wait(60)

    for _ in range(5):
        rig.dog.check()
        rig.wait(1)

    stale_alerts = [a for a in rig.alerts.alerts if a[0] == "market_data.stale"]
    assert len(stale_alerts) == 1 and SBIN in stale_alerts[0][1]


def test_an_out_of_order_tick_still_proves_the_feed_is_alive() -> None:
    rig = Rig()
    rig.tick(SBIN)
    rig.wait(9)
    late = make_tick(rig.clock.now() - timedelta(seconds=30), instrument_id=SBIN, out_of_order=True)
    rig.dog.on_tick(replace(late, received_ts=rig.clock.now()))  # old exchange time, arrived now
    rig.wait(9)
    assert not rig.dog.is_stale(SBIN)  # 9s since the (late) tick arrived


def test_an_instrument_that_never_ticks_goes_stale_measured_from_the_session_open() -> None:
    rig = Rig(ist(9, 15, 5))

    assert not rig.dog.is_stale(SBIN)
    rig.clock.set(ist(9, 15, 12))  # 12s after the open, still no tick at all
    assert rig.dog.is_stale(SBIN)  # HIGH allowance is 10s


def test_yesterdays_last_tick_does_not_make_a_quiet_open_look_fresh() -> None:
    rig = Rig(ist(15, 29, 59))
    rig.tick(SBIN)
    rig.clock.set(ist(9, 15, 30, day=date(2026, 9, 21)))  # Monday, 30s into the session
    assert rig.dog.is_stale(SBIN)


@pytest.mark.parametrize(
    "moment", [ist(8, 0), ist(15, 30), ist(20, 0), ist(11, 0, day=date(2026, 9, 19))]
)
def test_outside_the_session_silence_means_nothing(moment: datetime) -> None:
    rig = Rig()
    rig.tick(SBIN)
    rig.clock.set(moment)
    assert not rig.dog.is_stale(SBIN) and rig.dog.silent_for(SBIN) is None
    assert rig.dog.check() == []


def test_a_dropped_feed_makes_everything_stale_and_recovery_needs_fresh_ticks() -> None:
    rig = Rig(watch=(SBIN, THIN))
    rig.tick(SBIN)
    rig.tick(THIN)

    asyncio.run(rig.dog.on_disconnected("socket closed"))
    assert rig.dog.stale_instruments() == frozenset({SBIN, THIN})  # nothing waited for a threshold

    asyncio.run(rig.dog.on_connected())
    rig.wait(1)
    assert rig.dog.stale_instruments() == frozenset()  # recent ticks are still recent
    rig.wait(60)
    assert SBIN in rig.dog.stale_instruments()  # and silence after the reconnect is caught as usual


def test_check_reports_recovery_when_the_feed_returns_without_a_tick() -> None:
    rig = Rig(watch=(SBIN, THIN))
    rig.tick(SBIN)
    rig.tick(THIN)
    asyncio.run(rig.dog.on_disconnected("x"))
    assert [c.stale for c in rig.dog.check()] == [True, True]

    asyncio.run(rig.dog.on_connected())
    assert {(c.instrument_id, c.stale) for c in rig.dog.check()} == {(SBIN, False), (THIN, False)}


def test_only_watched_instruments_are_ever_stale_and_unwatching_forgets_them() -> None:
    rig = Rig(watch=(SBIN, THIN))
    rig.wait(3600)
    assert not rig.dog.is_stale("NSE:NEVER_WATCHED")

    rig.dog.unwatch([SBIN])
    assert not rig.dog.is_stale(SBIN) and rig.dog.stale_instruments() == frozenset({THIN})


def test_no_ticks_a_minute_after_the_open_raises_the_alarm_once_for_the_day() -> None:
    rig = Rig(ist(9, 15, 30))
    rig.dog.check()
    assert not [a for a in rig.alerts.alerts if a[0] == "market_data.no_ticks_at_open"]

    rig.clock.set(ist(9, 16, 5))
    rig.dog.check()
    rig.dog.check()

    assert [a[0] for a in rig.alerts.alerts].count("market_data.no_ticks_at_open") == 1


def test_no_open_alarm_when_ticks_are_flowing() -> None:
    rig = Rig(ist(9, 15, 30))
    rig.tick(SBIN)
    rig.clock.set(ist(9, 16, 30))
    rig.dog.check()
    assert not [a for a in rig.alerts.alerts if a[0] == "market_data.no_ticks_at_open"]


def test_threshold_configuration_is_validated() -> None:
    with pytest.raises(ConfigurationError):
        TieredThresholds(seconds_by_tier={LiquidityTier.MEDIUM: 0.0})
    with pytest.raises(ConfigurationError):
        TieredThresholds({"X": LiquidityTier.LOW}, {LiquidityTier.MEDIUM: 30.0})
