"""Headers for the market-data WebSocket (plan.md §1.5): the jwt, API key, client code and feed
token. Kept apart from the socket client so the client depends only on the small
`FeedAuthProvider` Protocol, and reconnect can ask for a *refreshed* feed token."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from emporos.broker.angelone.session_manager import SessionManager


@dataclass(frozen=True)
class FeedAuth:
    """The four credentials the socket handshake needs. None of them appear in a `repr`."""

    jwt: str = field(repr=False)
    api_key: str = field(repr=False)
    client_code: str = field(repr=False)
    feed_token: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.jwt}",
            "x-api-key": self.api_key,
            "x-client-code": self.client_code,
            "x-feed-token": self.feed_token,
        }


class FeedAuthProvider(Protocol):
    async def feed_auth(self, *, refresh: bool) -> FeedAuth:
        """Current credentials; `refresh=True` obtains a fresh feed token first (token refresh,
        and only if that is refused, a full re-login)."""
        ...


class SessionFeedAuthProvider:
    def __init__(self, sessions: SessionManager, api_key: str, client_code: str) -> None:
        self._sessions = sessions
        self._api_key = api_key
        self._client_code = client_code

    async def feed_auth(self, *, refresh: bool) -> FeedAuth:
        if refresh:
            await self._sessions.refresh_feed_token()
        session = await self._sessions.session()
        return FeedAuth(session.jwt, self._api_key, self._client_code, session.feed_token)
