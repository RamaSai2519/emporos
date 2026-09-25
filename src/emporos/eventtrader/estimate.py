"""What a Dev run would ask for, before a single call is made (EM-240).

Sizes come from the real stage requests, rendered for the real events with a real prompt; only the
answers are assumed (their length, and how many events triage passes on, which is unknown until
triage has been answered). Tokens are characters over 3.5, a little pessimistic for English and
JSON. Variants share calls through the journal: t60 and t75 ask triage and the panel the same
questions, so v1..v5 together cost triage + panel + judge on the t60 passers + the no-panel judge on
the same events (v3), plus one posture call a session (v4 and v5 share them)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.stages.models import PanelView, Side
from emporos.eventtrader.stages.prompts import BEAR_V1, BULL_V1, JUDGE_V1, TAPE_V1, TRIAGE_V1
from emporos.eventtrader.stages.stages import EventInput, JudgeInput
from emporos.jev.prompts import JevPrompt

__all__ = ["CHARS_PER_TOKEN", "Estimate", "Stage", "estimate_run"]

CHARS_PER_TOKEN = Decimal("3.5")
TRIAGE_OUT, PANEL_OUT, JUDGE_OUT, POSTURE_OUT = 70, 60, 90, 40
POSTURE_IN_CHARS = 2600  # the system prompt and about 30 numbers
BATCH_DISCOUNT = Decimal("0.5")
RETRY_SHARE = Decimal("0.02")  # replies that need the one reminder retry
_VIEW = PanelView("bull", Side.LONG, 70, None, "x" * 110)


@dataclass(frozen=True)
class Stage:
    name: str
    calls: int
    tokens_in: int
    tokens_out: int
    usd: Decimal


@dataclass(frozen=True)
class Estimate:
    pass_rate: float
    stages: tuple[Stage, ...]
    usd_batch: Decimal

    @property
    def usd(self) -> Decimal:
        return sum((s.usd for s in self.stages), Decimal(0))

    @property
    def calls(self) -> int:
        return sum(s.calls for s in self.stages)


def _chars(system: JevPrompt, user: dict[str, object]) -> int:
    return len(system.system_text) + len(json.dumps(user, sort_keys=True, separators=(",", ":")))


def _tokens(chars: int) -> int:
    return int(Decimal(chars) / CHARS_PER_TOKEN)


def _stage(name: str, calls: Decimal, chars: Decimal, out: int, model: str, p: PriceTable) -> Stage:
    n = int(calls * (1 + RETRY_SHARE))
    tin = int(chars * (1 + RETRY_SHARE) / CHARS_PER_TOKEN)
    tout = n * out
    return Stage(name, n, tin, tout, p.cost_usd(model, tin, tout))


def estimate_run(
    items: Sequence[EventInput], sessions: int, pass_rates: Sequence[float], prices: PriceTable,
    model: str,
) -> list[Estimate]:  # fmt: skip
    """One estimate per assumed triage pass rate, for v1..v5 together."""
    triage = sum(_chars(TRIAGE_V1, item.render()) for item in items)
    persona = sum(
        _chars(prompt, item.render()) for item in items for prompt in (BULL_V1, BEAR_V1, TAPE_V1)
    )
    with_views = sum(_chars(JUDGE_V1, JudgeInput(item, [_VIEW] * 3).render()) for item in items)
    without = sum(_chars(JUDGE_V1, JudgeInput(item, []).render()) for item in items)
    n = len(items)
    out: list[Estimate] = []
    for rate in pass_rates:
        share = Decimal(str(rate))
        n_pass = Decimal(n) * share

        def stage(name: str, calls: Decimal, chars: int, out_tokens: int, scale: Decimal) -> Stage:
            return _stage(name, calls, Decimal(chars) * scale, out_tokens, model, prices)

        stages = (
            stage("triage", Decimal(n), triage, TRIAGE_OUT, Decimal(1)),
            stage("panel (3 personas)", n_pass * 3, persona, PANEL_OUT, share),
            stage("judge with panel (v1, v2, v4, v5)", n_pass, with_views, JUDGE_OUT, share),
            stage("judge without panel (v3)", n_pass, without, JUDGE_OUT, share),
            stage("posture (v4, v5)", Decimal(sessions), sessions * POSTURE_IN_CHARS, POSTURE_OUT,
                  Decimal(1)),
        )  # fmt: skip
        total = sum((s.usd for s in stages), Decimal(0))
        out.append(Estimate(rate, stages, total * BATCH_DISCOUNT))
    return out
