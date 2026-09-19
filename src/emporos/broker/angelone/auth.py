"""Login, token renewal and logout against SmartAPI (EM-46).

Uses the `RestTransport` stack it is given, so login inherits rate limiting (1/s) and the
transport's classification. `loginByPassword`, `generateTokens` and `logout` are all *mutating*
endpoints: the retry layer replays them only when the request provably never left this process.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.session import Credentials, Session, SessionExpiry, TotpSource
from emporos.broker.angelone.transport import RestRequest, RestTransport
from emporos.broker.errors import BrokerProtocolError
from emporos.core.clock import Clock


class AngelOneAuthenticator:
    def __init__(
        self,
        transport: RestTransport,
        credentials: Credentials,
        totp: TotpSource,
        clock: Clock,
        expiry: SessionExpiry | None = None,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._totp = totp
        self._clock = clock
        self._expiry = expiry or SessionExpiry()

    async def login(self) -> Session:
        data = await self._transport.send(
            RestRequest(
                Endpoints.LOGIN,
                body={
                    "clientcode": self._credentials.client_code,
                    "password": self._credentials.pin,
                    "totp": self._totp.code(),
                },
            )
        )
        now = self._clock.now()
        return self._session_from(data, established_at=now, expires_at=self._expiry.expires_at(now))

    async def renew(self, session: Session) -> Session:
        """Mid-day refresh via `generateTokens`: new jwt AND feed token, no TOTP or full login.

        The midnight expiry of the original login is kept — renewing does not extend the day."""
        data = await self._transport.send(
            RestRequest(
                Endpoints.GENERATE_TOKENS,
                body={"refreshToken": session.refresh_token},
                bearer=session.jwt,
            )
        )
        return self._session_from(
            data, established_at=self._clock.now(), expires_at=session.expires_at
        )

    async def logout(self, session: Session) -> None:
        await self._transport.send(
            RestRequest(
                Endpoints.LOGOUT,
                body={"clientcode": self._credentials.client_code},
                bearer=session.jwt,
            )
        )

    @staticmethod
    def _session_from(data: Any, *, established_at: datetime, expires_at: datetime) -> Session:
        tokens = _tokens(data)
        return Session(
            jwt=tokens["jwtToken"],
            refresh_token=tokens["refreshToken"],
            feed_token=tokens["feedToken"],
            established_at=established_at,
            expires_at=expires_at,
        )


def _tokens(data: Any) -> Mapping[str, str]:
    """A "successful" reply without all three tokens is not a session — it is a protocol fault."""
    if not isinstance(data, Mapping):
        raise BrokerProtocolError("login reply has no token payload")
    tokens: dict[str, str] = {}
    for key in ("jwtToken", "refreshToken", "feedToken"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise BrokerProtocolError(f"login reply is missing {key}")
        tokens[key] = value
    return tokens
