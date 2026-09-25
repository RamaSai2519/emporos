"""The five stages (EM-240): Triage, the three panellists, the Judge, the Arbiter and the daily
Posture. Each is a `JsonStage` that renders its input and validates the model's JSON."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from emporos.core.clock import IST
from emporos.eventtrader.events import MarketContext, MarketEvent
from emporos.eventtrader.llm.client import LlmClient
from emporos.eventtrader.llm.reply import InvalidReply, Reply
from emporos.eventtrader.stages.base import JsonStage
from emporos.eventtrader.stages.models import (
    ArbiterResult,
    Horizon,
    Instrument,
    JudgeResult,
    PanelView,
    Posture,
    PostureResult,
    Side,
    TriageDirection,
    TriageResult,
)
from emporos.eventtrader.stages.prompts import (
    ARBITER_V1,
    BEAR_V1,
    BULL_V1,
    JUDGE_V1,
    POSTURE_V2,
    TAPE_V1,
    TRIAGE_V1,
)
from emporos.jev.prompts import JevPrompt

__all__ = [
    "ArbiterInput",
    "ArbiterStage",
    "EventInput",
    "JudgeInput",
    "JudgeStage",
    "PanelistStage",
    "PostureInput",
    "PostureStage",
    "TriageStage",
]

MAX_TEXT_CHARS = 3000
_INSTRUMENTS = [i.value for i in Instrument] + ["none"]
_SIDES = [s.value for s in Side] + ["none"]


@dataclass(frozen=True)
class EventInput:
    event: MarketEvent
    context: MarketContext
    decision_at: datetime  # the moment the decision is made (for the prompt's clock and the guard)

    def render(self) -> dict[str, Any]:
        """Only what was public by `decision_at`: the item, how long ago it appeared, the time of
        day and the as-of market numbers. No calendar date."""
        minutes = max(0, int((self.decision_at - self.event.usable_from).total_seconds() // 60))
        return {
            "item": {
                "kind": self.event.kind,
                "company": self.event.symbol,
                "category": self.event.category,
                "subject": self.event.subject,
                "text": self.event.text[:MAX_TEXT_CHARS],
                "minutes_since_it_appeared": minutes,
            },
            "time_of_day_ist": self.decision_at.astimezone(IST).strftime("%H:%M"),
            "market_numbers": {k: _value(v) for k, v in sorted(self.context.lines.items())},
        }


def _value(v: str | float) -> str | float:
    """Compact numbers: a prompt is billed by the token, and a fifth decimal is not information."""
    if not isinstance(v, float):
        return v
    return round(v) if abs(v) >= 1000 else round(v, 2)


class TriageStage(JsonStage[EventInput, TriageResult]):
    def __init__(self, client: LlmClient, model: str, prompt: JevPrompt = TRIAGE_V1) -> None:
        super().__init__("triage", client, model, prompt, 200)

    def render(self, item: EventInput) -> Mapping[str, Any]:
        return item.render()

    def parse(self, reply: Reply) -> TriageResult:
        return TriageResult(
            reply.boolean("material"),
            TriageDirection(reply.choice("direction", [d.value for d in TriageDirection])),
            reply.number("expected_move_pct", 0, 50),
            Horizon(reply.choice("horizon", [h.value for h in Horizon])),
            reply.boolean("priced_in"),
            int(reply.number("confidence", 0, 100)),
            reply.text("reason"),
        )


class PanelistStage(JsonStage[EventInput, PanelView]):
    """One persona. Build the bull, the bear and the tape reader with their own prompts."""

    def __init__(self, persona: str, client: LlmClient, model: str, prompt: JevPrompt) -> None:
        super().__init__(persona, client, model, prompt, 250)
        self._persona = persona

    @classmethod
    def bull(cls, client: LlmClient, model: str) -> PanelistStage:
        return cls("bull", client, model, BULL_V1)

    @classmethod
    def bear(cls, client: LlmClient, model: str) -> PanelistStage:
        return cls("bear", client, model, BEAR_V1)

    @classmethod
    def tape(cls, client: LlmClient, model: str) -> PanelistStage:
        return cls("tape", client, model, TAPE_V1)

    def render(self, item: EventInput) -> Mapping[str, Any]:
        return item.render()

    def parse(self, reply: Reply) -> PanelView:
        side = reply.choice("side", _SIDES)
        instrument = reply.choice("instrument", _INSTRUMENTS)
        conviction = int(reply.number("conviction", 0, 100))
        if (side == "none") != (instrument == "none"):
            raise InvalidReply("side and instrument must both be none, or neither")
        return PanelView(
            self._persona,
            None if side == "none" else Side(side),
            conviction,
            None if instrument == "none" else Instrument(instrument),
            reply.text("reason"),
        )


@dataclass(frozen=True)
class JudgeInput:
    event: EventInput
    views: Sequence[PanelView]  # empty when the panel is switched off

    def render(self) -> dict[str, Any]:
        out = self.event.render()
        out["panel"] = [
            {"role": v.persona, "side": v.side.value if v.side else "none",
             "conviction": v.conviction,
             "instrument": v.instrument.value if v.instrument else "none", "reason": v.reason}
            for v in self.views
        ]  # fmt: skip
        return out


class JudgeStage(JsonStage[JudgeInput, JudgeResult]):
    def __init__(self, client: LlmClient, model: str, prompt: JevPrompt = JUDGE_V1) -> None:
        super().__init__("judge", client, model, prompt, 300)

    def render(self, item: JudgeInput) -> Mapping[str, Any]:
        return item.render()

    def parse(self, reply: Reply) -> JudgeResult:
        trade = reply.boolean("trade")
        instrument = reply.choice("instrument", _INSTRUMENTS)
        side = reply.choice("side", _SIDES)
        stop, target = reply.number("stop_pct", 0, 15), reply.number("target_pct", 0, 40)
        hold = int(reply.number("hold_days", 0, 20))
        confidence = int(reply.number("confidence", 0, 100))
        reason = reply.text("reason")
        if not trade:
            return JudgeResult(False, None, None, 0.0, 0.0, 0, confidence, reason)
        if instrument == "none" or side == "none":
            raise InvalidReply("a trade needs an instrument and a side")
        if stop <= 0 or target <= 0:
            raise InvalidReply("a trade needs a positive stop and target")
        if Instrument(instrument) is Instrument.CASH_INTRADAY and hold != 0:
            raise InvalidReply("an intraday trade is held 0 days")
        if Instrument(instrument) is Instrument.CASH_SWING and hold < 1:
            raise InvalidReply("a swing trade is held at least 1 day")
        return JudgeResult(
            True, Instrument(instrument), Side(side), stop, target, hold, confidence, reason
        )


@dataclass(frozen=True)
class ArbiterInput:
    judge: JudgeInput
    proposal: JudgeResult

    def render(self) -> dict[str, Any]:
        out = self.judge.render()
        p = self.proposal
        out["proposed_trade"] = {
            "instrument": p.instrument.value if p.instrument else "none",
            "side": p.side.value if p.side else "none", "stop_pct": p.stop_pct,
            "target_pct": p.target_pct, "hold_days": p.hold_days, "reason": p.reason,
        }  # fmt: skip
        return out


class ArbiterStage(JsonStage[ArbiterInput, ArbiterResult]):
    def __init__(self, client: LlmClient, model: str, prompt: JevPrompt = ARBITER_V1) -> None:
        super().__init__("arbiter", client, model, prompt, 250)

    def render(self, item: ArbiterInput) -> Mapping[str, Any]:
        return item.render()

    def parse(self, reply: Reply) -> ArbiterResult:
        return ArbiterResult(
            reply.boolean("approve"), int(reply.number("confidence", 0, 100)), reply.text("reason")
        )


@dataclass(frozen=True)
class PostureInput:
    headlines: Sequence[str]  # the previous evening's, newest last, already trimmed by the caller
    state: Mapping[str, str | float]  # NIFTY / INDIA VIX / global cues, as of the morning
    decision_at: datetime

    def render(self) -> dict[str, Any]:
        return {
            "time_of_day_ist": self.decision_at.astimezone(IST).strftime("%H:%M"),
            "headlines": list(self.headlines)[-40:],
            "state": {k: _value(v) for k, v in sorted(self.state.items())},
        }


class PostureStage(JsonStage[PostureInput, PostureResult]):
    def __init__(self, client: LlmClient, model: str, prompt: JevPrompt = POSTURE_V2) -> None:
        super().__init__("posture", client, model, prompt, 150)

    def render(self, item: PostureInput) -> Mapping[str, Any]:
        return item.render()

    def parse(self, reply: Reply) -> PostureResult:
        return PostureResult(
            Posture(reply.choice("posture", [p.value for p in Posture])), reply.text("reason")
        )
