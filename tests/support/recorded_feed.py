"""Replays a REAL recorded market-feed session (EM-186) through our own pipeline.

The fixtures were captured from the live Angel One feed by `scripts/record_angelone_frames.py`
(market data only). Replaying them through the real parser, normalizer and aggregator, and reading
the broker's own candles for the same minutes from the companion fixture, is what lets the live
candle-construction check run without a broker or a market.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path

from emporos.broker.angelone.ws_frames import TickFrameParser, TimestampDecoder
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.aggregator import MinuteCandleAggregator
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.queue import QueuedTick
from tests.support.fakes import make_instrument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "angelone"


class RecordedFrames:
    def __init__(self, path: Path = FIXTURES / "live_feed_2026-09-24.json") -> None:
        self._data = json.loads(path.read_text())

    @property
    def tokens(self) -> list[str]:
        return list(self._data["tokens"])

    @property
    def events(self) -> list[dict[str, str]]:
        return list(self._data["events"])

    def frames(self) -> list[tuple[datetime, bytes]]:
        return [
            (datetime.fromisoformat(f["arrival_utc"]), base64.b64decode(f["b64"]))
            for f in self._data["frames"]
        ]


class RecordedSessionReplay:
    """Feeds the recorded frames, at their recorded arrival times, through parse -> normalize ->
    aggregate and returns the closed 1m candles."""

    def __init__(
        self, recording: RecordedFrames, timestamps: TimestampDecoder | None = None
    ) -> None:
        self._recording = recording
        self._parser = TickFrameParser(timestamps)

    def candles(self) -> list[Candle]:
        frames = self._recording.frames()
        clock = FixedClock(frames[0][0])
        aggregator = MinuteCandleAggregator(clock)
        out = _Collector()
        aggregator.subscribe(out)
        normalizer = TickNormalizer(
            InstrumentCache([make_instrument(t) for t in self._recording.tokens])
        )
        for arrived, frame in frames:
            clock.set(arrived)
            tick = normalizer.normalize(QueuedTick(self._parser.parse(frame), arrived))
            if tick is not None:
                aggregator.on_tick(tick)
            aggregator.advance()
        aggregator.advance(clock.now() + timedelta(minutes=2))
        return out.candles


class _Collector:
    def __init__(self) -> None:
        self.candles: list[Candle] = []

    def on_candle(self, candle: Candle) -> None:
        self.candles.append(candle)


def broker_history(
    timeframe: Timeframe = Timeframe.M1, path: Path = FIXTURES / "candles_2026-09-24.json"
) -> list[Candle]:
    interval = {Timeframe.M1: "ONE_MINUTE", Timeframe.M5: "FIVE_MINUTE"}[timeframe]
    history = json.loads(path.read_text())["history"]
    bars: list[Candle] = []
    for key, rows in history.items():
        token, name = key.split(":")
        if name != interval:
            continue
        for ts, o, h, low, c, volume in rows:
            bars.append(
                Candle(
                    instrument_id=f"NSE:{token}", timeframe=timeframe,
                    ts=datetime.fromisoformat(ts), open=Money.of(o), high=Money.of(h),
                    low=Money.of(low), close=Money.of(c), volume=volume,
                )
            )  # fmt: skip
    return bars
