"""Builds SmartWebSocketV2 binary frames for tests, per the layout documented in
`emporos.broker.angelone.ws_frames` (and cross-checked against the SDK parser as an oracle).

No frame here was recorded live — that needs an open market session."""

from __future__ import annotations

import struct
from datetime import UTC, datetime

_HEADER = struct.Struct("<BB25sqqq")
_QUOTE = struct.Struct("<qqqddqqqq")
LTP_MODE, QUOTE_MODE, SNAP_QUOTE_MODE, DEPTH_MODE = 1, 2, 3, 4
NSE_CM, BSE_CM, NSE_FO = 1, 3, 2


def epoch_ms(moment: datetime) -> int:
    return int(moment.astimezone(UTC).timestamp() * 1000)


DEFAULT_TS_MS = epoch_ms(datetime(2026, 9, 18, 4, 30, tzinfo=UTC))  # 10:00 IST


class FrameBuilder:
    @staticmethod
    def header(
        mode: int,
        token: str | bytes = "3045",
        exchange_type: int = NSE_CM,
        sequence: int = 1,
        ts_ms: int = DEFAULT_TS_MS,
        ltp_paise: int = 99620,
    ) -> bytes:
        raw = token if isinstance(token, bytes) else token.encode("ascii")
        return _HEADER.pack(mode, exchange_type, raw.ljust(25, b"\x00"), sequence, ts_ms, ltp_paise)

    @classmethod
    def ltp(cls, **fields: object) -> bytes:
        return cls.header(LTP_MODE, **fields)  # type: ignore[arg-type]

    @classmethod
    def quote(
        cls,
        *,
        last_qty: int = 25,
        avg_paise: int = 99189,
        volume: int = 5_699_456,
        buy_qty: float = 0.0,
        sell_qty: float = 408.0,
        day_open: int = 99200,
        day_high: int = 99620,
        day_low: int = 98510,
        prev_close: int = 98870,
        mode: int = QUOTE_MODE,
        **header: object,
    ) -> bytes:
        body = _QUOTE.pack(
            last_qty, avg_paise, volume, buy_qty, sell_qty, day_open, day_high, day_low, prev_close
        )
        return cls.header(mode, **header) + body  # type: ignore[arg-type]

    @classmethod
    def snap_quote(cls, **fields: object) -> bytes:
        """QUOTE fields plus the 256 trailing bytes (OI, best-5 depth, circuits, 52-week)."""
        return cls.quote(mode=SNAP_QUOTE_MODE, **fields) + bytes(256)  # type: ignore[arg-type]

    @classmethod
    def depth(cls) -> bytes:
        return cls.header(DEPTH_MODE) + bytes(200)
