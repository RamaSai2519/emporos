"""EM-46: login, renewal and logout requests and parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.session import Credentials
from emporos.broker.errors import BrokerAuthError, BrokerProtocolError
from emporos.core.clock import IST, FixedClock
from tests.support.fakes import FixedTotp, ScriptedRestTransport, token_payload

NOW = datetime(2026, 9, 21, 9, 15, tzinfo=IST)


def build(transport: ScriptedRestTransport) -> AngelOneAuthenticator:
    return AngelOneAuthenticator(
        transport, Credentials("C123", "9876"), FixedTotp("246810"), FixedClock(NOW)
    )


async def test_login_sends_client_code_pin_and_a_fresh_totp() -> None:
    transport = ScriptedRestTransport(loginByPassword=[token_payload("a")])

    session = await build(transport).login()

    (request,) = transport.sent_to("loginByPassword")
    assert request.body == {"clientcode": "C123", "password": "9876", "totp": "246810"}
    assert request.bearer is None
    assert (session.jwt, session.refresh_token, session.feed_token) == (
        "jwt-a",
        "refresh-a",
        "feed-a",
    )
    assert session.established_at == NOW
    assert session.expires_at == datetime(2026, 9, 22, 0, 0, tzinfo=IST)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "ok",
        [],
        {},
        {"jwtToken": "j", "refreshToken": "r"},
        {**token_payload(), "feedToken": ""},
    ],
)
async def test_a_success_without_all_three_tokens_is_a_protocol_fault(payload: object) -> None:
    transport = ScriptedRestTransport(loginByPassword=[payload])

    with pytest.raises(BrokerProtocolError):
        await build(transport).login()


async def test_a_rejected_login_propagates_as_an_auth_error() -> None:
    transport = ScriptedRestTransport(
        loginByPassword=[BrokerAuthError("Invalid totp", code="AB1050")]
    )

    with pytest.raises(BrokerAuthError) as raised:
        await build(transport).login()
    assert raised.value.code == "AB1050"


async def test_renew_uses_the_refresh_token_and_keeps_the_original_midnight() -> None:
    transport = ScriptedRestTransport(
        loginByPassword=[token_payload("a")], generateTokens=[token_payload("b")]
    )
    auth = build(transport)
    original = await auth.login()

    renewed = await auth.renew(original)

    (request,) = transport.sent_to("generateTokens")
    assert request.body == {"refreshToken": "refresh-a"}
    assert request.bearer == "jwt-a"
    assert (renewed.jwt, renewed.feed_token) == ("jwt-b", "feed-b")
    assert renewed.expires_at == original.expires_at
    assert len(transport.sent_to("loginByPassword")) == 1  # no second login


async def test_logout_names_the_client_and_presents_the_token() -> None:
    transport = ScriptedRestTransport(loginByPassword=[token_payload()], logout=[None])
    auth = build(transport)
    session = await auth.login()

    await auth.logout(session)

    (request,) = transport.sent_to("logout")
    assert request.body == {"clientcode": "C123"}
    assert request.bearer == session.jwt
