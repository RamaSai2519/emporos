"""EM-49: the binary tick parser and the reader that must never crash."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporos.broker.angelone.ws_frames import (
    EpochMillisIstWallClock,
    EpochMillisUtc,
    MalformedFrameError,
    TickFrameParser,
    TickFrameReader,
    UnsupportedFrameError,
)
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.ticks import RawTick
from tests.support.fakes import RecordingAlertSink
from tests.support.frames import BSE_CM, DEFAULT_TS_MS, NSE_FO, FrameBuilder

PARSER = TickFrameParser()
TEN_AM_UTC = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)


def test_an_ltp_frame_parses_to_exact_values() -> None:
    tick = PARSER.parse(FrameBuilder.ltp(token="3045", sequence=42, ltp_paise=99620))

    assert (tick.exchange, tick.token, tick.sequence) == (Exchange.NSE, "3045", 42)
    assert tick.exchange_ts == TEN_AM_UTC
    assert tick.ltp == Money.of("996.20") and isinstance(tick.ltp.amount, Decimal)
    assert tick.volume is None and tick.quote is None  # LTP mode carries no volume


def test_a_quote_frame_adds_volume_and_the_day_fields() -> None:
    tick = PARSER.parse(FrameBuilder.quote(last_qty=25, volume=5_699_456, sell_qty=408.0))

    assert (tick.volume, tick.last_traded_quantity) == (5_699_456, 25)
    assert tick.quote is not None
    assert tick.quote.average_price == Money.of("991.89")
    assert (tick.quote.day_open, tick.quote.day_high) == (Money.of("992.00"), Money.of("996.20"))
    assert (tick.quote.day_low, tick.quote.previous_close) == (
        Money.of("985.10"),
        Money.of("988.70"),
    )
    assert (tick.quote.total_buy_quantity, tick.quote.total_sell_quantity) == (
        Decimal(0),
        Decimal(408),
    )


def test_a_snap_quote_frame_yields_the_quote_view_and_ignores_the_rest() -> None:
    tick = PARSER.parse(FrameBuilder.snap_quote(volume=7))
    assert tick.volume == 7 and tick.quote is not None


def test_the_bse_cash_exchange_and_a_full_width_token_are_handled() -> None:
    tick = PARSER.parse(FrameBuilder.ltp(token="A" * 25, exchange_type=BSE_CM))
    assert tick.exchange is Exchange.BSE and tick.token == "A" * 25


def test_paise_convert_exactly_with_no_float_rounding() -> None:
    assert PARSER.parse(FrameBuilder.ltp(ltp_paise=1)).ltp.amount == Decimal("0.01")
    assert PARSER.parse(FrameBuilder.ltp(ltp_paise=10)).ltp.amount == Decimal("0.10")
    assert PARSER.parse(FrameBuilder.ltp(ltp_paise=123456789)).ltp.amount == Decimal("1234567.89")


@pytest.mark.parametrize(
    "frame",
    [
        FrameBuilder.depth(),
        FrameBuilder.ltp(exchange_type=NSE_FO),
        FrameBuilder.ltp(exchange_type=13),
    ],
)
def test_frames_outside_v1_scope_are_unsupported_not_malformed(frame: bytes) -> None:
    with pytest.raises(UnsupportedFrameError):
        PARSER.parse(frame)


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"\x01",
        FrameBuilder.ltp()[:50],  # one byte short
        FrameBuilder.ltp() + b"\x00",  # one byte long
        FrameBuilder.quote()[:-1],
        FrameBuilder.snap_quote()[:-1],
        FrameBuilder.quote()[:51],  # a QUOTE header with an LTP-length body
        FrameBuilder.header(9),  # unknown mode
        FrameBuilder.header(0),
        FrameBuilder.ltp(token=""),  # empty token
        FrameBuilder.ltp(token=b"\xff\xfe"),  # not ASCII
        FrameBuilder.ltp(token=b"a\x01b"),  # control character
        FrameBuilder.ltp(ltp_paise=0),
        FrameBuilder.ltp(ltp_paise=-5),
        FrameBuilder.ltp(ts_ms=0),  # 1970: implausible
        FrameBuilder.ltp(ts_ms=-1),
        FrameBuilder.ltp(ts_ms=2**62),  # far future / overflow
        FrameBuilder.quote(volume=-1),
        FrameBuilder.quote(last_qty=-1),
        FrameBuilder.quote(avg_paise=-1),
        FrameBuilder.quote(buy_qty=float("nan")),
        FrameBuilder.quote(sell_qty=float("inf")),
    ],
)
def test_malformed_frames_raise_only_the_typed_error(frame: bytes) -> None:
    with pytest.raises(MalformedFrameError):
        PARSER.parse(frame)


def test_the_two_timestamp_readings_differ_by_exactly_the_ist_offset() -> None:
    utc = EpochMillisUtc().decode(DEFAULT_TS_MS)
    ist = EpochMillisIstWallClock().decode(DEFAULT_TS_MS)
    assert utc - ist == timedelta(hours=5, minutes=30)
    assert TickFrameParser(EpochMillisIstWallClock()).parse(FrameBuilder.ltp()).exchange_ts == ist


@given(frame=st.binary(max_size=600))
def test_arbitrary_bytes_never_raise_anything_but_the_typed_errors(frame: bytes) -> None:
    try:
        tick = PARSER.parse(frame)
    except (MalformedFrameError, UnsupportedFrameError):
        return
    assert isinstance(tick, RawTick)  # or it was a genuinely valid frame


@given(
    frame=st.sampled_from([FrameBuilder.ltp(), FrameBuilder.quote(), FrameBuilder.snap_quote()]),
    flips=st.lists(st.tuples(st.integers(0, 400), st.integers(0, 255)), min_size=1, max_size=8),
)
def test_corrupting_a_valid_frame_never_crashes_the_parser(
    frame: bytes, flips: list[tuple[int, int]]
) -> None:
    damaged = bytearray(frame)
    for index, value in flips:
        if index < len(damaged):
            damaged[index] = value
    with contextlib.suppress(MalformedFrameError, UnsupportedFrameError):
        PARSER.parse(bytes(damaged))


@given(
    token=st.text(alphabet="0123456789", min_size=1, max_size=25),
    seq=st.integers(0, 2**62),
    ltp=st.integers(1, 10**12),
    volume=st.integers(0, 10**12),
    ts_ms=st.integers(1_500_000_000_000, 4_000_000_000_000),
)
def test_valid_frames_round_trip_every_field(
    token: str, seq: int, ltp: int, volume: int, ts_ms: int
) -> None:
    tick = PARSER.parse(
        FrameBuilder.quote(token=token, sequence=seq, ltp_paise=ltp, volume=volume, ts_ms=ts_ms)
    )

    assert (tick.token, tick.sequence, tick.volume) == (token, seq, volume)
    assert tick.ltp.amount * 100 == ltp
    assert tick.exchange_ts == datetime.fromtimestamp(ts_ms / 1000, tz=UTC)


class Sink:
    def __init__(self) -> None:
        self.ticks: list[RawTick] = []

    def on_raw_tick(self, tick: RawTick) -> None:
        self.ticks.append(tick)


def test_the_reader_forwards_good_frames_and_drops_and_counts_the_rest() -> None:
    sink, alerts = Sink(), RecordingAlertSink()
    reader = TickFrameReader(PARSER, sink, alerts)

    for frame in (
        FrameBuilder.ltp(sequence=1),
        b"garbage",
        FrameBuilder.depth(),
        FrameBuilder.ltp(sequence=2),
    ):
        reader.on_frame(frame)  # none of these may raise

    assert [t.sequence for t in sink.ticks] == [1, 2]
    counts = reader.counts
    assert (counts.parsed, counts.malformed, counts.unsupported) == (2, 1, 1)


def test_malformed_frames_alarm_at_powers_of_ten_not_on_every_frame() -> None:
    alerts = RecordingAlertSink()
    reader = TickFrameReader(PARSER, Sink(), alerts)

    for _ in range(105):
        reader.on_frame(b"bad")

    assert [name for name, _ in alerts.alerts] == [
        "market_data.malformed_frames"
    ] * 3  # at 1, 10, 100
    assert "100 malformed" in alerts.alerts[-1][1]


def test_the_reader_works_without_an_alert_sink() -> None:
    reader = TickFrameReader(PARSER, Sink())
    reader.on_frame(b"bad")
    assert reader.counts.malformed == 1
