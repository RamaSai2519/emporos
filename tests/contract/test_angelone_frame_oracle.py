"""EM-49/EM-54: our binary parser vs the pinned SDK's parser, on the same frames (Decision 4).

The two implementations were written independently; agreeing field-for-field on every frame
shape is strong evidence the layout is right. What it cannot prove is that live frames follow
the layout — that needs recorded frames from an open session (pending)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emporos.broker.angelone.ws_frames import TickFrameParser
from emporos.domain.instruments import Exchange
from tests.support.frames import BSE_CM, NSE_CM, FrameBuilder
from tests.support.sdk_oracle import sdk_parse_frame

pytestmark = pytest.mark.contract

PARSER = TickFrameParser()


def paise_to_rupees(paise: int) -> Decimal:
    return Decimal(paise).scaleb(-2)


def assert_agrees(frame: bytes, workdir: Path) -> None:
    sdk = sdk_parse_frame(frame, workdir)
    ours = PARSER.parse(frame)

    assert ours.token == sdk["token"]
    assert ours.sequence == sdk["sequence_number"]
    assert int(ours.exchange_ts.timestamp() * 1000) == sdk["exchange_timestamp"]
    assert ours.ltp.amount == paise_to_rupees(sdk["last_traded_price"])
    assert ours.exchange is {NSE_CM: Exchange.NSE, BSE_CM: Exchange.BSE}[sdk["exchange_type"]]
    if "volume_trade_for_the_day" in sdk:
        assert ours.volume == sdk["volume_trade_for_the_day"]
        assert ours.last_traded_quantity == sdk["last_traded_quantity"]
        assert ours.quote is not None
        assert ours.quote.average_price.amount == paise_to_rupees(sdk["average_traded_price"])
        assert ours.quote.total_buy_quantity == Decimal(repr(sdk["total_buy_quantity"]))
        assert ours.quote.total_sell_quantity == Decimal(repr(sdk["total_sell_quantity"]))
        assert ours.quote.day_open.amount == paise_to_rupees(sdk["open_price_of_the_day"])
        assert ours.quote.day_high.amount == paise_to_rupees(sdk["high_price_of_the_day"])
        assert ours.quote.day_low.amount == paise_to_rupees(sdk["low_price_of_the_day"])
        assert ours.quote.previous_close.amount == paise_to_rupees(sdk["closed_price"])
    else:
        assert ours.volume is None and ours.quote is None


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(FrameBuilder.ltp(), id="ltp"),
        pytest.param(
            FrameBuilder.ltp(token="500325", exchange_type=BSE_CM, ltp_paise=250075), id="ltp-bse"
        ),
        pytest.param(FrameBuilder.quote(), id="quote"),
        pytest.param(
            FrameBuilder.quote(buy_qty=1234.5, sell_qty=99.0, volume=1), id="quote-floats"
        ),
        pytest.param(FrameBuilder.snap_quote(), id="snap-quote"),
    ],
)
def test_our_parser_agrees_with_the_sdk_on_every_in_scope_frame_shape(
    frame: bytes, tmp_path: Path
) -> None:
    assert_agrees(frame, tmp_path)


@settings(max_examples=60, deadline=None)
@given(
    token=st.text(alphabet="0123456789", min_size=1, max_size=12),
    seq=st.integers(0, 2**40),
    ltp=st.integers(1, 10**9),
    volume=st.integers(0, 10**10),
    last_qty=st.integers(0, 10**7),
    ts_ms=st.integers(1_600_000_000_000, 2_000_000_000_000),
)
def test_agreement_holds_across_random_valid_quote_frames(
    token: str,
    seq: int,
    ltp: int,
    volume: int,
    last_qty: int,
    ts_ms: int,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    frame = FrameBuilder.quote(
        token=token, sequence=seq, ltp_paise=ltp, volume=volume, last_qty=last_qty, ts_ms=ts_ms
    )
    assert_agrees(frame, tmp_path_factory.mktemp("sdk"))
