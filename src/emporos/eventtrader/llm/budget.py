"""A hard USD ceiling on paid calls (EM-240; the operator has USD 4 on the OpenAI key).

Checked BEFORE each call against the worst case that call could cost: every character of the
prompt counted as a token (a real token is never shorter than a character) plus the full output
allowance. It raises rather than degrading, so a run that stopped asking cannot pass for a model
that declined everything."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from emporos.core.errors import DefinitiveError
from emporos.eventtrader.llm.client import LlmClient, LlmReply, LlmRequest
from emporos.eventtrader.llm.journal import Recording
from emporos.eventtrader.llm.pricing import PriceTable

__all__ = ["BudgetRefused", "BudgetedClient", "UsdBudget", "journal_spend_usd", "worst_case_usd"]


class BudgetRefused(DefinitiveError):
    """The next call could take the spend past the declared ceiling."""


def worst_case_usd(
    prices: PriceTable, model: str, calls: int, prompt_chars: int, max_output_tokens: int
) -> Decimal:
    """The most `calls` calls of this size could cost (chars are an upper bound on tokens)."""
    return prices.cost_usd(model, prompt_chars, max_output_tokens) * calls


def journal_spend_usd(recordings: Iterable[Recording], prices: PriceTable) -> Decimal:
    """What the journal's calls cost when they were made: every recording is one call that reached
    the model, at list price. A ceiling that outlives one process is measured on this, so a re-run
    cannot spend the same allowance twice."""
    return sum(
        (prices.cost_usd(r.model, r.tokens_in, r.tokens_out) for r in recordings), Decimal(0)
    )


@dataclass
class UsdBudget:
    """The ceiling counts what is spent, plus what calls in flight could still cost at most: with
    many calls at once the ceiling is a bound, not something to overshoot by a few calls."""

    ceiling_usd: Decimal
    spent_usd: Decimal = Decimal(0)
    calls: int = 0
    reserved_usd: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.ceiling_usd <= 0:
            raise ValueError("a budget ceiling must be positive")

    @property
    def remaining_usd(self) -> Decimal:
        return self.ceiling_usd - self.spent_usd - self.reserved_usd

    def reserve(self, worst_case: Decimal) -> None:
        if self.spent_usd + self.reserved_usd + worst_case > self.ceiling_usd:
            raise BudgetRefused(
                f"the next call could cost ${worst_case:.4f}, which would take the spend "
                f"(${self.spent_usd + self.reserved_usd:.4f}) past the ${self.ceiling_usd} ceiling"
            )
        self.reserved_usd += worst_case

    def release(self, worst_case: Decimal) -> None:
        """The call did not happen or failed before it could cost anything."""
        self.reserved_usd -= worst_case

    def record(self, cost_usd: Decimal, worst_case: Decimal = Decimal(0)) -> None:
        self.reserved_usd -= worst_case
        self.spent_usd += cost_usd
        self.calls += 1


class BudgetedClient:
    """Counts only calls that reach `inner`: stack it inside the journal, so a recorded answer is
    free."""

    def __init__(self, inner: LlmClient, budget: UsdBudget, prices: PriceTable) -> None:
        self._inner, self._budget, self._prices = inner, budget, prices

    async def complete(self, request: LlmRequest) -> LlmReply:
        prompt_chars = len(request.system) + len(request.user)
        worst = self._prices.cost_usd(request.model, prompt_chars, request.max_output_tokens)
        self._budget.reserve(worst)
        try:
            reply = await self._inner.complete(request)
        except BaseException:
            self._budget.release(worst)
            raise
        self._budget.record(
            self._prices.cost_usd(reply.model, reply.tokens_in, reply.tokens_out), worst
        )
        return reply
