"""The three atlas calls as `LlmStage`s on the Track L JSON stage (EM-245): render only what the
guard lets through, ask once (once more on an invalid reply), parse strictly. No call here reaches
a model unless a client is passed in; tests use fakes until the head declares the run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from emporos.core.clock import IST
from emporos.eventtrader.llm.client import LlmClient
from emporos.eventtrader.llm.reply import Reply
from emporos.eventtrader.stages.base import JsonStage
from emporos.jev.prompts import JevPrompt
from emporos.research.attribution.coverage import Coverage
from emporos.research.attribution.guard import (
    assert_public,
    candidates,
    post_onset,
    public_by,
)
from emporos.research.attribution.items import CauseItem
from emporos.research.attribution.prompts import ATTRIBUTION_V1, CROSSING_CAUSE_V1, DIRECTION_V1
from emporos.research.attribution.results import (
    NO_ITEM,
    Attribution,
    CrossingCall,
    DirectionRead,
    Outlook,
    Way,
)
from emporos.research.attribution.taxonomy import Driver

__all__ = [
    "AttributionStage", "CauseOnly", "CrossingCase", "CrossingCauseStage", "DirectionStage",
    "MoveCase", "check_citation",
]  # fmt: skip

_DRIVERS = [d.value for d in Driver]
_HIDDEN_FROM_B = ("move", "return", "residual", "path", "price", "gap")


def _numbers(numbers: Mapping[str, float]) -> dict[str, float]:
    return {k: round(v, 3) for k, v in sorted(numbers.items())}


def _item_or_none(reply: Reply) -> str | None:
    cited = reply.text("cited_item", 80)
    return None if cited == NO_ITEM else cited


@dataclass(frozen=True)
class MoveCase:
    """Call A's input: a move already seen (hindsight), and the items around its onset."""

    who: str
    scope: str  # "stock", "sector" or "index"
    direction: int
    onset: datetime
    numbers: Mapping[str, float]  # the decomposition, the peers, the sector and market
    items: Sequence[CauseItem]


@dataclass(frozen=True)
class CrossingCase:
    """Call C's input: a crossing and only what was public up to it."""

    who: str
    direction: int
    crossed_at: datetime
    move_so_far: Mapping[str, float]  # the path to the crossing, never after
    numbers: Mapping[str, float]  # market, sector and group at that moment
    items: Sequence[CauseItem]


@dataclass(frozen=True)
class CauseOnly:
    """Call B's input: one item and the numbers at the moment it became public, no price move."""

    item: CauseItem
    numbers: Mapping[str, float]

    def __post_init__(self) -> None:
        leaks = [k for k in self.numbers if any(w in k.lower() for w in _HIDDEN_FROM_B)]
        if leaks:
            raise ValueError(f"Call B is blind to the move: {leaks} are move numbers")


class AttributionStage(JsonStage[MoveCase, Attribution]):
    def __init__(
        self, client: LlmClient, model: str, coverage: Coverage | None = None,
        prompt: JevPrompt = ATTRIBUTION_V1,
    ) -> None:  # fmt: skip
        super().__init__("attribution", client, model, prompt, 300)
        self._coverage = coverage or Coverage()

    def render(self, item: MoveCase) -> Mapping[str, Any]:
        shown = candidates(item.items, item.onset)
        return {
            "move": {
                "who": item.who, "scope": item.scope,
                "direction": "up" if item.direction > 0 else "down",
                "time_of_day_ist": item.onset.astimezone(IST).strftime("%H:%M"),
                **_numbers(item.numbers),
            },
            "items": [i.render(item.onset) for i in shown],
            "not_available": self._coverage.lines(),
        }  # fmt: skip

    def parse(self, reply: Reply) -> Attribution:
        secondary = reply.choice("secondary", [*_DRIVERS, "none"])
        return Attribution(
            Driver(reply.choice("primary", _DRIVERS)),
            None if secondary == "none" else Driver(secondary),
            int(reply.number("confidence", 0, 100)),
            _item_or_none(reply),
            reply.text("reason"),
        )


class CrossingCauseStage(JsonStage[CrossingCase, CrossingCall]):
    def __init__(
        self, client: LlmClient, model: str, coverage: Coverage | None = None,
        prompt: JevPrompt = CROSSING_CAUSE_V1,
    ) -> None:  # fmt: skip
        super().__init__("crossing-cause", client, model, prompt, 300)
        self._coverage = coverage or Coverage()

    def render(self, item: CrossingCase) -> Mapping[str, Any]:
        assert_public(item.items, item.crossed_at)  # a leak is an error, never a quiet filter
        return {
            "crossing": {
                "who": item.who, "direction": "up" if item.direction > 0 else "down",
                "time_of_day_ist": item.crossed_at.astimezone(IST).strftime("%H:%M"),
                "move_so_far": _numbers(item.move_so_far), "now": _numbers(item.numbers),
            },
            "items": [i.render(item.crossed_at) for i in public_by(item.items, item.crossed_at)],
            "not_available": self._coverage.lines(),
        }  # fmt: skip

    def parse(self, reply: Reply) -> CrossingCall:
        return CrossingCall(
            Driver(reply.choice("driver", _DRIVERS)),
            Outlook(reply.choice("outlook", [o.value for o in Outlook])),
            int(reply.number("confidence", 0, 100)),
            int(reply.number("horizon_minutes", 0, 1500)),
            _item_or_none(reply),
            reply.text("reason"),
        )


class DirectionStage(JsonStage[CauseOnly, DirectionRead]):
    def __init__(
        self, client: LlmClient, model: str, coverage: Coverage | None = None,
        prompt: JevPrompt = DIRECTION_V1,
    ) -> None:  # fmt: skip
        super().__init__("direction", client, model, prompt, 200)
        self._coverage = coverage or Coverage()

    def render(self, item: CauseOnly) -> Mapping[str, Any]:
        return {
            "item": item.item.render(item.item.available_at),
            "numbers_then": _numbers(item.numbers),
            "not_available": self._coverage.lines(),
        }

    def parse(self, reply: Reply) -> DirectionRead:
        return DirectionRead(
            Way(reply.choice("way", [w.value for w in Way])),
            int(reply.number("horizon_minutes", 0, 7500)),
            int(reply.number("confidence", 0, 100)),
            reply.text("reason"),
        )


def check_citation(cited: str | None, items: Sequence[CauseItem], onset: datetime) -> str:
    """Code, not the model, checks a cited item: `none`, `unknown` (an id not among the items:
    an invented citation), `before_onset`, or `post_onset` (public after the move began)."""
    if cited is None:
        return "none"
    found = {i.item_id: i for i in items}.get(cited)
    if found is None:
        return "unknown"
    return "post_onset" if post_onset(found, onset) else "before_onset"
