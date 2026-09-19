"""EM-46: the authenticated transport attaches tokens and recovers a rejected session once."""

from __future__ import annotations

from datetime import datetime

import pytest

from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.authenticated import AuthenticatedTransport
from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.session import Credentials, InMemorySessionStore
from emporos.broker.angelone.session_manager import SessionManager
from emporos.broker.angelone.transport import RestRequest
from emporos.broker.errors import BrokerSessionExpiredError
from emporos.core.clock import IST, FixedClock
from tests.support.fakes import FixedTotp, ScriptedRestTransport, token_payload


class Rig:
    """`raw` stands in for the rate-limited, retrying transport underneath everything."""

    def __init__(self, **script: list[object]) -> None:
        clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=IST))
        self.raw = ScriptedRestTransport(**script)
        auth = AngelOneAuthenticator(self.raw, Credentials("C", "1"), FixedTotp(), clock)
        self.transport = AuthenticatedTransport(
            self.raw, SessionManager(auth, InMemorySessionStore(), clock)
        )


def bearers(rig: Rig, endpoint_name: str) -> list[str | None]:
    return [r.bearer for r in rig.raw.sent_to(endpoint_name)]


async def test_a_secured_call_logs_in_and_carries_the_token() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], getProfile=[{"clientcode": "C"}])

    data = await rig.transport.send(RestRequest(Endpoints.PROFILE))

    assert data == {"clientcode": "C"}
    assert bearers(rig, "getProfile") == ["jwt-a"]


async def test_the_login_call_itself_needs_no_session() -> None:
    rig = Rig(loginByPassword=[token_payload("a")])

    await rig.transport.send(RestRequest(Endpoints.LOGIN, body={}))

    assert len(rig.raw.requests) == 1  # no recursive login


async def test_an_explicit_token_is_respected() -> None:
    rig = Rig(getProfile=[{}])

    await rig.transport.send(RestRequest(Endpoints.PROFILE, bearer="mine"))

    assert bearers(rig, "getProfile") == ["mine"]
    assert rig.raw.sent_to("loginByPassword") == []


async def test_a_rejected_token_on_a_read_is_renewed_and_replayed_once() -> None:
    rig = Rig(
        loginByPassword=[token_payload("a")],
        generateTokens=[token_payload("b")],
        getProfile=[BrokerSessionExpiredError("Invalid Token"), {"clientcode": "C"}],
    )

    data = await rig.transport.send(RestRequest(Endpoints.PROFILE))

    assert data == {"clientcode": "C"}
    assert bearers(rig, "getProfile") == ["jwt-a", "jwt-b"]
    assert len(rig.raw.sent_to("loginByPassword")) == 1  # renewed, not re-logged-in


async def test_the_replay_is_bounded_to_one() -> None:
    rig = Rig(
        loginByPassword=[token_payload("a")],
        generateTokens=[token_payload("b")],
        getProfile=[BrokerSessionExpiredError("x"), BrokerSessionExpiredError("y")],
    )

    with pytest.raises(BrokerSessionExpiredError):
        await rig.transport.send(RestRequest(Endpoints.PROFILE))

    assert len(rig.raw.sent_to("getProfile")) == 2


async def test_a_mutating_call_with_a_rejected_token_is_not_replayed() -> None:
    rig = Rig(
        loginByPassword=[token_payload("a")],
        logout=[BrokerSessionExpiredError("Invalid Token")],
    )

    with pytest.raises(BrokerSessionExpiredError):
        await rig.transport.send(RestRequest(Endpoints.LOGOUT, body={}))

    assert len(rig.raw.sent_to("logout")) == 1
    assert rig.raw.sent_to("generateTokens") == []
