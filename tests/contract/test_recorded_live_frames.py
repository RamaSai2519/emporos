"""EM-186: frames recorded from the REAL Angel One market feed, against the SDK oracle.

`live_feed_2026-09-24.json` was captured from an open session (market data only, no order endpoint)
by `scripts/record_angelone_frames.py`. This closes the "needs recorded frames from an open
session" gap in `test_angelone_frame_oracle.py`: our decoder and the pinned SDK are compared on
bytes that really came off the wire, and the exchange timestamp epoch is checked against the time
each frame actually arrived.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from emporos.broker.angelone.ws_frames import EpochMillisIstWallClock, TickFrameParser
from tests.contract.test_angelone_frame_oracle import assert_agrees
from tests.support.recorded_feed import RecordedFrames

pytestmark = pytest.mark.contract

RECORDING = RecordedFrames()
FRAMES: Sequence[tuple[datetime, bytes]] = RECORDING.frames()


def test_the_recording_is_a_real_multi_instrument_quote_sample() -> None:
    assert len(FRAMES) >= 200
    assert {len(frame) for _, frame in FRAMES} == {123}  # QUOTE-mode frames, all the same size
    tokens = {TickFrameParser().parse(frame).token for _, frame in FRAMES}
    assert tokens == set(RECORDING.tokens)


@pytest.mark.parametrize("index", range(0, len(FRAMES), 7))
def test_our_parser_agrees_with_the_sdk_on_frames_from_the_live_wire(
    index: int, tmp_path: Path
) -> None:
    assert_agrees(FRAMES[index][1], tmp_path)


def test_exchange_timestamps_are_utc_epoch_millis_not_ist_wall_clock() -> None:
    """Live evidence for the epoch question (plan.md 7): arrival minus exchange time is seconds
    when read as UTC epoch, and exactly 5h30 off when read as IST wall clock."""
    utc, ist = TickFrameParser(), TickFrameParser(EpochMillisIstWallClock())
    lags = [(arrived - utc.parse(frame).exchange_ts).total_seconds() for arrived, frame in FRAMES]
    assert all(-1.0 <= lag <= 10.0 for lag in lags)
    ist_lags = [
        (arrived - ist.parse(frame).exchange_ts).total_seconds() for arrived, frame in FRAMES
    ]
    assert all(abs(lag - 19800) <= 10.0 for lag in ist_lags)


def test_the_controlled_drop_reconnected_and_resubscribed_within_seconds() -> None:
    names = [e["event"] for e in RECORDING.events]
    assert names == [
        "connected", "subscribed", "controlled_drop", "disconnected", "connected", "subscribed"
    ]  # fmt: skip
    at = {i: datetime.fromisoformat(e["at_utc"]) for i, e in enumerate(RECORDING.events)}
    assert (at[4] - at[3]).total_seconds() < 5.0
    assert (at[5] - at[4]).total_seconds() < 1.0
    resumed = [a for a, _ in FRAMES if a > at[5]]
    assert resumed, "no frames arrived after the resubscription"
