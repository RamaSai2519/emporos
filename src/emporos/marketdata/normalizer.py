"""Raw ticks -> normalized `Tick`s (EM-51, plan.md §7), applied in this order:

1. resolve `(exchange, token)` to an `instrument_id`; unknown tokens are dropped and counted;
2. drop ticks whose exchange time is outside the session window;
3. drop duplicates — same (instrument, exchange time, price) seen within the previous second;
4. flag out-of-order ticks (exchange time earlier than the latest already seen) and count them.
   They are still emitted — they belong in the trade record — but flagged, so nothing downstream
   may fold them into a candle that has already closed.

Times: `received_ts` is stamped at queue entry (by the injected `Clock`); `exchange_ts` is UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from emporos.domain.instruments import InstrumentResolver, UnknownInstrumentError
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.marketdata.queue import QueuedTick
from emporos.marketdata.session import SessionWindow

DEDUPE_WINDOW = timedelta(seconds=1)


@dataclass(frozen=True)
class NormalizerStats:
    accepted: int
    unknown_instrument: int
    outside_session: int
    duplicates: int
    out_of_order: int


class TickNormalizer:
    def __init__(
        self,
        resolver: InstrumentResolver,
        window: SessionWindow | None = None,
        dedupe_window: timedelta = DEDUPE_WINDOW,
    ) -> None:
        self._resolver = resolver
        self._window = window or SessionWindow()
        self._dedupe_window = dedupe_window
        self._latest: dict[str, datetime] = {}
        self._recent: dict[str, dict[tuple[datetime, Money], datetime]] = {}
        self._counts = {"accepted": 0, "unknown": 0, "session": 0, "duplicate": 0, "late": 0}

    @property
    def stats(self) -> NormalizerStats:
        c = self._counts
        return NormalizerStats(c["accepted"], c["unknown"], c["session"], c["duplicate"], c["late"])

    @property
    def dedupe_entries(self) -> int:
        """How many recent (time, price) keys are remembered — bounded by the 1s window."""
        return sum(len(seen) for seen in self._recent.values())

    def normalize(self, queued: QueuedTick) -> Tick | None:
        raw = queued.tick
        try:
            instrument_id = self._resolver.by_token(raw.exchange, raw.token).instrument_id
        except UnknownInstrumentError:
            self._counts["unknown"] += 1
            return None
        if not self._window.contains(raw.exchange_ts):
            self._counts["session"] += 1
            return None
        if self._is_duplicate(instrument_id, raw.exchange_ts, raw.ltp, queued.received_ts):
            self._counts["duplicate"] += 1
            return None

        latest = self._latest.get(instrument_id)
        out_of_order = latest is not None and raw.exchange_ts < latest
        if out_of_order:
            self._counts["late"] += 1
        else:
            self._latest[instrument_id] = raw.exchange_ts
        self._counts["accepted"] += 1
        return Tick(
            instrument_id=instrument_id,
            exchange_ts=raw.exchange_ts,
            received_ts=queued.received_ts,
            ltp=raw.ltp,
            sequence=raw.sequence,
            volume=raw.volume,
            out_of_order=out_of_order,
        )

    def _is_duplicate(
        self, instrument_id: str, exchange_ts: datetime, ltp: Money, received_ts: datetime
    ) -> bool:
        seen = self._recent.setdefault(instrument_id, {})
        for key in [k for k, at in seen.items() if received_ts - at > self._dedupe_window]:
            del seen[key]
        key = (exchange_ts, ltp)
        if key in seen:
            return True
        seen[key] = received_ts
        return False
