"""The date guard and the token tally, as decorators over an `LlmClient` (EM-240).

The guard is the program's own `KnowledgeCutoffGuard`: nothing before a model's declared cutoff
plus 90 days may reach it or its recordings. It stays on for every call in this track, names in
the prompt or not (§12.1)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from emporos.eventtrader.llm.client import LlmClient, LlmReply, LlmRequest
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.jev.config import JevConfig
from emporos.jev.leakage import KnowledgeCutoffGuard

__all__ = ["CutoffGuardedClient", "TallyingClient", "TokenTally"]


class CutoffGuardedClient:
    def __init__(
        self, inner: LlmClient, guard: KnowledgeCutoffGuard, configs: dict[str, JevConfig]
    ) -> None:
        self._inner, self._guard, self._configs = inner, guard, configs

    async def complete(self, request: LlmRequest) -> LlmReply:
        config = self._configs.get(request.model)
        if config is None:
            raise ValueError(f"no declared knowledge cutoff for model {request.model!r}")
        self._guard.check_as_of(request.as_of, config)
        return await self._inner.complete(request)


@dataclass
class TokenTally:
    """Tokens by stage and model, for the report and for charging the P&L. A reply from the
    journal is counted too (`recorded_*`): replaying a recording costs nothing now, but the
    strategy's P&L must carry what the calls cost when they were made."""

    tokens_in: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tokens_out: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, request: LlmRequest, reply: LlmReply) -> None:
        key = f"{request.stage}|{reply.model}"
        self.tokens_in[key] += reply.tokens_in
        self.tokens_out[key] += reply.tokens_out
        self.calls[key] += 1

    def cost_inr(self, prices: PriceTable) -> Decimal:
        total = Decimal(0)
        for key, n_in in self.tokens_in.items():
            model = key.split("|", 1)[1]
            total += prices.cost_inr(model, n_in, self.tokens_out[key])
        return total

    def cost_inr_by_stage(self, prices: PriceTable) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(Decimal)
        for key, n_in in self.tokens_in.items():
            stage, model = key.split("|", 1)
            out[stage] += prices.cost_inr(model, n_in, self.tokens_out[key])
        return dict(out)


class TallyingClient:
    """Outermost decorator: counts every reply, journal or live."""

    def __init__(self, inner: LlmClient, tally: TokenTally) -> None:
        self._inner, self._tally = inner, tally

    async def complete(self, request: LlmRequest) -> LlmReply:
        reply = await self._inner.complete(request)
        self._tally.add(request, reply)
        return reply
