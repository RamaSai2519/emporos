"""EM-185: a session survives storage exactly, and both renderings say the same thing."""

from __future__ import annotations

import json

import pytest

from emporos.parity.codec import SessionParityCodec
from emporos.parity.report import MetricsTable, ParityDocument
from tests.unit.parity.test_service import Rig, mirrored_paper


async def _daily(**kw: object):  # type: ignore[no-untyped-def]
    rig = Rig().paper(mirrored_paper(0, **kw))  # type: ignore[arg-type]
    (daily,) = (await rig.service.daily(rig.day)).reports
    return rig, daily


async def test_a_session_round_trips_through_its_document_exactly() -> None:
    rig, daily = await _daily()
    codec = SessionParityCodec()

    session = codec.from_document(daily.payload)

    assert codec.to_document(session) == daily.payload
    assert (
        json.loads(json.dumps(daily.payload)) == daily.payload
    )  # JSON-safe: no Decimal, no datetime
    assert session.signals and session.trades


async def test_a_degraded_session_round_trips_with_its_unpaired_rows() -> None:
    _, daily = await _daily(filled=False, sell=False)
    codec = SessionParityCodec()

    session = codec.from_document(daily.payload)

    assert codec.to_document(session) == daily.payload
    assert [t.paper for t in session.trades] == [None]  # the backtest's trip, unpaired


def test_an_unknown_schema_or_a_malformed_document_is_refused() -> None:
    codec = SessionParityCodec()
    with pytest.raises(ValueError, match="schema"):
        codec.from_document({"schema_version": 99})
    with pytest.raises(ValueError, match="malformed"):
        codec.from_document(
            {
                "schema_version": 1,
                "strategy": "s",
                "run_id": "r",
                "session_date": "2026-01-05",
                "behaviour_hash": "h",
                "starting_cash": "1",
                "signals": ["nope"],
                "trades": [],
            }
        )


async def test_json_and_markdown_carry_the_same_verdict_and_numbers() -> None:
    rig, daily = await _daily(filled=False, sell=False)
    session = SessionParityCodec().from_document(daily.payload)
    doc = ParityDocument()

    as_json = doc.to_json(daily, [session])
    text = doc.markdown(daily, [session])

    assert json.loads(json.dumps(as_json)) == as_json
    assert as_json["verdict"] == daily.verdict.value
    assert as_json["metrics"] == MetricsTable().of(rig.service._analyzer.metrics([session]))
    assert f"**Verdict: {daily.verdict.value.upper()}**" in text
    for section in (
        "## Gates",
        "## Degradation",
        "## By symbol",
        "## Signal ledger",
        "## Trade ledger",
        "## Latency",
    ):
        assert section in text
    assert "MISSED" in text and "BACKTEST_ONLY_SIGNAL" in text  # non-matched rows are shown


async def test_a_session_with_no_paper_orders_says_so_in_the_latency_section() -> None:
    _, daily = await _daily(filled=False, sell=False)
    session = SessionParityCodec().from_document(daily.payload)
    # the missed BUY still produced an order, so only strip it to exercise the empty branch
    from dataclasses import replace

    bare = replace(
        session,
        signals=tuple(replace(r, paper_outcome=None) for r in session.signals),
    )
    assert "No paper orders were timed." in ParityDocument().markdown(daily, [bare])
