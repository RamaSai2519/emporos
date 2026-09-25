"""Scripted doubles for the Track L tests: an LLM client that answers from a script per stage, and
builders for events and contexts."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from emporos.eventtrader.events import FILING, MarketContext, MarketEvent
from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.eventtrader.llm.http_clients import LlmTransportError
from emporos.eventtrader.stages.stages import EventInput

NOON = datetime(2024, 3, 4, 6, 30, tzinfo=UTC)  # 12:00 IST on a Monday


def event(**overrides: object) -> MarketEvent:
    values: dict[str, object] = {
        "event_id": "E1", "instrument_id": "NSE:2885", "symbol": "RELIANCE",
        "published_at": NOON - timedelta(minutes=12), "usable_from": NOON - timedelta(minutes=12),
        "kind": FILING, "category": "Outcome", "subject": "Financial results",
        "text": "Revenue up 12%, profit up 20%.",
    }  # fmt: skip
    return MarketEvent(**{**values, **overrides})  # type: ignore[arg-type]


def item(**overrides: object) -> EventInput:
    return EventInput(
        event(**overrides),
        MarketContext({"move_since_close_pct": 1.23456789, "vwap_dist_pct": 0.4}),
        NOON,
    )  # fmt: skip


def reply(**fields: object) -> str:
    return json.dumps(fields)


TRIAGE_OK = reply(
    material=True, direction="up", expected_move_pct=3.0, horizon="swing", priced_in=False,
    confidence=80, reason="strong beat",
)  # fmt: skip


def view(side: str = "long", instrument: str = "cash_swing", conviction: int = 70) -> str:
    return reply(side=side, conviction=conviction, instrument=instrument, reason="because")


def judge(
    trade: bool = True, instrument: str = "cash_swing", side: str = "long", **kw: object
) -> str:
    fields: dict[str, object] = {
        "trade": trade, "instrument": instrument if trade else "none",
        "side": side if trade else "none", "stop_pct": 3.0 if trade else 0,
        "target_pct": 6.0 if trade else 0, "hold_days": 5 if trade else 0, "confidence": 75,
        "reason": "clear edge",
    }  # fmt: skip
    return reply(**{**fields, **kw})


class ScriptedLlm:
    """Answers each stage from its own queue of texts (or exceptions); records every request."""

    def __init__(self, **scripts: list[str | Exception]) -> None:
        self._scripts = {k: deque(v) for k, v in scripts.items()}
        self.requests: list[LlmRequest] = []
        self.calls: dict[str, int] = defaultdict(int)

    async def complete(self, request: LlmRequest) -> LlmReply:
        self.requests.append(request)
        self.calls[request.stage] += 1
        queue = self._scripts.get(request.stage)
        if not queue:
            raise AssertionError(f"no scripted reply left for stage {request.stage!r}")
        answer = queue.popleft()
        if isinstance(answer, Exception):
            raise answer
        return LlmReply(answer, 100, 20, request.model)


def transport_error() -> LlmTransportError:
    return LlmTransportError("boom")
