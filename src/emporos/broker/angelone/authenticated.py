"""Attaches the session token to requests and recovers from a rejected one (EM-46)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from emporos.broker.angelone.session_manager import SessionProvider
from emporos.broker.angelone.transport import RestRequest, RestTransport
from emporos.broker.errors import BrokerSessionExpiredError


class AuthenticatedTransport:
    """A `RestTransport` that callers use without ever touching tokens.

    If the server rejects the token mid-day, the session is renewed and a read is replayed once.
    A *mutating* call is not replayed automatically (it surfaces the retryable
    `BrokerSessionExpiredError`): replaying state-changing calls is the execution engine's
    decision, made with idempotency in hand (Decision 7), never the transport's."""

    def __init__(self, inner: RestTransport, sessions: SessionProvider) -> None:
        self._inner = inner
        self._sessions = sessions

    async def send(self, request: RestRequest) -> Any:
        if not request.endpoint.authenticated or request.bearer is not None:
            return await self._inner.send(request)
        session = await self._sessions.session()
        try:
            return await self._inner.send(replace(request, bearer=session.jwt))
        except BrokerSessionExpiredError:
            if request.endpoint.mutates:
                raise
            renewed = await self._sessions.renew(session)
            return await self._inner.send(replace(request, bearer=renewed.jwt))
