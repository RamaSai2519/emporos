"""EM-51: normalization exactly per plan.md §7 — resolve, session-filter, dedupe, flag late."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.queue import QueuedTick
from tests.support.fakes import make_instrument, raw_tick

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)  # 10:00:00 IST, a Friday
SBIN, RELIANCE = make_instrument("3045"), make_instrument("2885")


def normalizer() -> TickNormalizer:
    return TickNormalizer(InstrumentCache([SBIN, RELIANCE]))


def feed(
    n: TickNormalizer,
    token: str = "3045",
    *,
    at: datetime,
    arrived: datetime | None = None,
    ltp: str = "996.20",
    seq: int = 1,
):  # type: ignore[no-untyped-def]
    tick = raw_tick(token, at=at, ltp=ltp, sequence=seq, volume=100)
    return n.normalize(QueuedTick(tick, arrived or at))


def test_a_good_tick_is_normalized_with_its_instrument_id_and_both_timestamps() -> None:
    arrived = T0 + timedelta(milliseconds=40)

    tick = feed(normalizer(), at=T0, arrived=arrived, ltp="996.20", seq=9)

    assert tick is not None
    assert tick.instrument_id == "NSE:3045"
    assert (tick.exchange_ts, tick.received_ts) == (T0, arrived)
    assert tick.ltp == Money.of("996.20") and tick.ltp.amount == Decimal("996.20")
    assert (tick.sequence, tick.volume, tick.out_of_order) == (9, 100, False)


def test_an_unknown_token_is_dropped_and_counted_never_raised() -> None:
    n = normalizer()
    assert feed(n, "999999", at=T0) is None
    assert n.stats.unknown_instrument == 1 and n.stats.accepted == 0


def test_the_same_token_on_another_exchange_resolves_separately() -> None:
    n = TickNormalizer(InstrumentCache([make_instrument("500", Exchange.NSE)]))
    bse = raw_tick("500", at=T0, exchange=Exchange.BSE)
    assert n.normalize(QueuedTick(bse, T0)) is None  # only NSE:500 is known
    assert n.stats.unknown_instrument == 1


def test_ticks_outside_the_regular_session_are_dropped() -> None:
    n = normalizer()
    before_open = datetime(2026, 9, 18, 3, 44, 59, tzinfo=UTC)  # 09:14:59 IST
    at_close = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)  # 15:30:00 IST
    saturday = datetime(2026, 9, 19, 4, 30, tzinfo=UTC)

    assert [feed(n, at=t) for t in (before_open, at_close, saturday)] == [None, None, None]
    assert feed(n, at=datetime(2026, 9, 18, 3, 45, tzinfo=UTC)) is not None  # 09:15:00 IST: in
    assert n.stats.outside_session == 3 and n.stats.accepted == 1


def test_a_duplicate_within_a_second_is_dropped_but_a_different_price_or_time_is_not() -> None:
    n = normalizer()
    first = feed(n, at=T0, ltp="996.20")
    same = feed(n, at=T0, arrived=T0 + timedelta(milliseconds=500), ltp="996.20")
    other_price = feed(n, at=T0, arrived=T0 + timedelta(milliseconds=600), ltp="996.25")
    other_time = feed(
        n, at=T0 + timedelta(milliseconds=1), arrived=T0 + timedelta(milliseconds=700), ltp="996.20"
    )

    assert first is not None and same is None
    assert other_price is not None and other_time is not None
    assert n.stats.duplicates == 1


def test_an_identical_tick_more_than_a_second_later_is_a_new_tick() -> None:
    n = normalizer()
    feed(n, at=T0, arrived=T0)
    late = feed(n, at=T0, arrived=T0 + timedelta(seconds=1, milliseconds=1))
    assert late is not None and n.stats.duplicates == 0


def test_duplicates_are_judged_per_instrument() -> None:
    n = normalizer()
    assert feed(n, "3045", at=T0) is not None
    assert feed(n, "2885", at=T0) is not None  # same time and price, different instrument


def test_dedupe_memory_stays_bounded_over_a_long_session() -> None:
    n = normalizer()
    for second in range(0, 3000, 2):  # a tick every 2s for 50 minutes, all distinct
        moment = T0 + timedelta(seconds=second)
        feed(n, at=moment, arrived=moment, ltp=f"{996 + second / 1000:.3f}")
    assert n.dedupe_entries <= 2  # only the last second's keys are remembered


def test_an_out_of_order_tick_is_counted_flagged_and_still_emitted() -> None:
    n = normalizer()
    feed(n, at=T0 + timedelta(seconds=10), ltp="997.00", seq=2)

    late = feed(n, at=T0 + timedelta(seconds=5), ltp="996.50", seq=1)

    assert late is not None and late.out_of_order is True
    assert n.stats.out_of_order == 1 and n.stats.accepted == 2


def test_a_late_tick_does_not_move_the_latest_time_so_later_ticks_are_not_flagged() -> None:
    n = normalizer()
    feed(n, at=T0 + timedelta(seconds=10), ltp="997.00")
    feed(n, at=T0 + timedelta(seconds=5), ltp="996.50")  # late

    on_time = feed(n, at=T0 + timedelta(seconds=11), ltp="997.10")

    assert on_time is not None and on_time.out_of_order is False


def test_equal_exchange_times_are_not_out_of_order() -> None:
    n = normalizer()
    feed(n, at=T0, ltp="996.20")
    tie = feed(n, at=T0, arrived=T0 + timedelta(milliseconds=5), ltp="996.30")
    assert tie is not None and tie.out_of_order is False


def test_out_of_order_is_tracked_per_instrument() -> None:
    n = normalizer()
    feed(n, "3045", at=T0 + timedelta(seconds=10))
    other = feed(n, "2885", at=T0 + timedelta(seconds=1))  # earlier, but another instrument
    assert other is not None and other.out_of_order is False
