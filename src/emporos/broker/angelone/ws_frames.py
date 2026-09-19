"""SmartWebSocketV2 binary frame parser and the reader that feeds it (EM-49).

Wire layout, little-endian (verified against the pinned SDK's parser as an oracle — see
tests/contract/test_angelone_frame_oracle.py; NOT yet against recorded live frames, which need
an open market session):

    0      mode            uint8   1 LTP, 2 QUOTE, 3 SNAP_QUOTE, 4 DEPTH
    1      exchange type   uint8   1 NSE_CM, 3 BSE_CM (others: not in v1 scope)
    2-26   token           25 bytes, NUL-terminated ASCII
    27-34  sequence        int64
    35-42  exchange ts     int64   epoch milliseconds
    43-50  last price      int64   paise
    -- QUOTE and SNAP_QUOTE continue --
    51-58  last qty        int64
    59-66  average price   int64   paise
    67-74  day volume      int64
    75-82  total buy qty   float64
    83-90  total sell qty  float64
    91-98  day open        int64   paise        (99-106 high, 107-114 low, 115-122 prev close)
    -- SNAP_QUOTE has 256 more bytes (OI, best-5 depth, circuits) that v1 does not consume --

Frame lengths are exact (51 / 123 / 379): a different length means the layout changed, and a
frame we cannot vouch for is dropped and counted rather than half-trusted.
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.broker.angelone.ws_market import ExchangeType, FeedMode
from emporos.broker.errors import BrokerProtocolError
from emporos.core.alerts import AlertSink
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.ticks import QuoteData, RawTick

_LOG = logging.getLogger(__name__)

_LENGTH_BY_MODE = {FeedMode.LTP: 51, FeedMode.QUOTE: 123, FeedMode.SNAP_QUOTE: 379}
_EXCHANGE_BY_TYPE = {ExchangeType.NSE_CM: Exchange.NSE, ExchangeType.BSE_CM: Exchange.BSE}
_HEADER = struct.Struct("<BB25sqqq")  # mode, exchange type, token, sequence, exchange ts, ltp
_QUOTE = struct.Struct("<qqqddqqqq")  # last qty, avg, volume, buy qty, sell qty, o, h, l, close
_MIN_TS = datetime(2015, 1, 1, tzinfo=UTC)
_MAX_TS = datetime(2100, 1, 1, tzinfo=UTC)
_IST_OFFSET = timedelta(hours=5, minutes=30)


class MalformedFrameError(BrokerProtocolError):
    """The frame is not a valid tick. It is dropped and counted; it never stops the reader."""


class UnsupportedFrameError(BrokerProtocolError):
    """A well-formed frame outside v1 scope (DEPTH, derivatives, commodities)."""


class TimestampDecoder(Protocol):
    def decode(self, epoch_ms: int) -> datetime:
        """The exchange timestamp as timezone-aware UTC."""
        ...


class EpochMillisUtc:
    """`exchange_timestamp` is milliseconds since the Unix epoch (UTC). This is the standard
    reading and the default; live verification during a session is pending (see plan.md §7,
    which words it as an IST epoch). If the live feed turns out to count IST wall-clock
    milliseconds, swap in `EpochMillisIstWallClock` — the normalizer's skew alarm flags it."""

    def decode(self, epoch_ms: int) -> datetime:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)


class EpochMillisIstWallClock:
    """The alternative reading: milliseconds counted from the epoch on the IST wall clock."""

    def decode(self, epoch_ms: int) -> datetime:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC) - _IST_OFFSET


def _money(paise: int) -> Money:
    return Money(Decimal(paise).scaleb(-2))  # exact: paise are integers


class TickFrameParser:
    def __init__(self, timestamps: TimestampDecoder | None = None) -> None:
        self._timestamps = timestamps or EpochMillisUtc()

    def parse(self, frame: bytes) -> RawTick:
        """Parse one frame; raises only `MalformedFrameError` or `UnsupportedFrameError`."""
        if len(frame) < _HEADER.size:
            raise MalformedFrameError(f"frame too short ({len(frame)} bytes)")
        mode_code, exchange_code, raw_token, sequence, exchange_ms, ltp_paise = _HEADER.unpack_from(
            frame
        )
        mode = self._mode(mode_code)
        exchange = self._exchange(exchange_code)
        if len(frame) != _LENGTH_BY_MODE[mode]:
            raise MalformedFrameError(
                f"{mode.name} frame must be {_LENGTH_BY_MODE[mode]} bytes, got {len(frame)}"
            )
        try:
            return self._build(frame, mode, exchange, raw_token, sequence, exchange_ms, ltp_paise)
        except (ValueError, ArithmeticError, OverflowError, OSError) as error:
            raise MalformedFrameError(f"invalid tick content: {type(error).__name__}") from None

    def _build(
        self,
        frame: bytes,
        mode: FeedMode,
        exchange: Exchange,
        raw_token: bytes,
        sequence: int,
        exchange_ms: int,
        ltp_paise: int,
    ) -> RawTick:
        token = self._token(raw_token)
        exchange_ts = self._timestamps.decode(exchange_ms)
        if not _MIN_TS <= exchange_ts <= _MAX_TS:
            raise MalformedFrameError("exchange timestamp is implausible")
        if ltp_paise <= 0:
            raise MalformedFrameError("last price must be positive")
        volume = quantity = None
        quote = None
        if mode in (FeedMode.QUOTE, FeedMode.SNAP_QUOTE):
            qty, avg, volume, buy, sell, day_open, high, low, close = _QUOTE.unpack_from(frame, 51)
            if volume < 0 or qty < 0 or min(avg, day_open, high, low, close) < 0:
                raise MalformedFrameError("negative quantity or price in a quote")
            if not (math.isfinite(buy) and math.isfinite(sell)):
                raise MalformedFrameError("non-finite buy/sell quantity in a quote")
            quantity = qty
            quote = QuoteData(
                _money(avg),
                Decimal(repr(buy)),  # the wire's float64 -> its shortest exact decimal text
                Decimal(repr(sell)),
                _money(day_open),
                _money(high),
                _money(low),
                _money(close),
            )
        return RawTick(
            exchange=exchange,
            token=token,
            exchange_ts=exchange_ts,
            ltp=_money(ltp_paise),
            sequence=sequence,
            volume=volume,
            last_traded_quantity=quantity,
            quote=quote,
        )

    @staticmethod
    def _mode(code: int) -> FeedMode:
        try:
            return FeedMode(code)
        except ValueError:
            if code == 4:  # DEPTH
                raise UnsupportedFrameError("DEPTH frames are out of v1 scope") from None
            raise MalformedFrameError(f"unknown subscription mode {code}") from None

    @staticmethod
    def _exchange(code: int) -> Exchange:
        try:
            return _EXCHANGE_BY_TYPE[ExchangeType(code)]
        except (ValueError, KeyError):
            raise UnsupportedFrameError(f"exchange type {code} is out of v1 scope") from None

    @staticmethod
    def _token(raw: bytes) -> str:
        text = raw.split(b"\x00", 1)[0]
        if not text or not text.isascii() or not text.decode("ascii").isprintable():
            raise MalformedFrameError("token is empty or not printable ASCII")
        return text.decode("ascii")


class TickSink(Protocol):
    def on_raw_tick(self, tick: RawTick) -> None: ...


@dataclass(frozen=True)
class FrameCounts:
    parsed: int
    malformed: int
    unsupported: int


class TickFrameReader:
    """The `FrameHandler`: parse, count, drop the bad, forward the good.

    A malformed frame is counted and logged, and raises an alarm when the running total reaches
    1, 10, 100, 1000... (a growing count means the layout changed or the feed is corrupt —
    visible without flooding the alert channel)."""

    def __init__(
        self, parser: TickFrameParser, sink: TickSink, alerts: AlertSink | None = None
    ) -> None:
        self._parser = parser
        self._sink = sink
        self._alerts = alerts
        self._parsed = 0
        self._malformed = 0
        self._unsupported = 0

    @property
    def counts(self) -> FrameCounts:
        return FrameCounts(self._parsed, self._malformed, self._unsupported)

    def on_frame(self, frame: bytes) -> None:
        try:
            tick = self._parser.parse(frame)
        except UnsupportedFrameError:
            self._unsupported += 1
            return
        except MalformedFrameError as error:
            self._malformed += 1
            _LOG.warning("dropping malformed market-data frame: %s", error.message)
            self._maybe_alert()
            return
        self._parsed += 1
        self._sink.on_raw_tick(tick)

    def _maybe_alert(self) -> None:
        count = self._malformed
        is_power_of_ten = count >= 1 and set(str(count)[1:]) <= {"0"} and str(count)[0] == "1"
        if self._alerts is not None and is_power_of_ten:
            self._alerts.raise_alert(
                "market_data.malformed_frames",
                f"{count} malformed market-data frames dropped so far",
            )
