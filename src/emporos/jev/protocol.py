"""The interface every Jev provider implements. `emporos.opportunity` depends on this Protocol,
never on `VercelGatewayJevClient` directly — the concrete adapter is wired in at the composition
root, exactly like a `Broker` implementation (plan.md's "Depend on abstractions" rule applies to
Jev too, even though it is not a broker).
"""

from __future__ import annotations

from typing import Protocol

from emporos.jev.models import JevDecision, JevRequest


class JevProvider(Protocol):
    async def decide(self, request: JevRequest) -> JevDecision: ...
