"""EM-46: the composition helper wires the full stack and fails clearly on missing settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pyotp
import pytest

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.angelone.transport import RestRequest
from emporos.core.clock import FixedClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from tests.support.fakes import (
    AdvancingSleeper,
    FixedJitter,
    ScriptedHttpServer,
    ok_reply,
    token_payload,
)

SECRET = pyotp.random_base32()


def settings(**overrides: str) -> Settings:
    values = {
        "ANGELONE_API_KEY": "the-api-key",
        "ANGELONE_CLIENT_CODE": "C123",
        "ANGELONE_PASSWORD": "4321",
        "ANGELONE_TOTP_SECRET": SECRET,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class ScriptedClientFactory:
    def __init__(self, server: ScriptedHttpServer) -> None:
        self._server = server

    def create(self) -> httpx.AsyncClient:
        return self._server.client()


def factory(server: ScriptedHttpServer, config: Settings) -> AngelOneStackFactory:
    clock = FixedClock(datetime(2026, 9, 21, 9, 15, tzinfo=UTC))
    return AngelOneStackFactory(
        config, clock, AdvancingSleeper(clock), FixedJitter(), ScriptedClientFactory(server)
    )


async def test_the_wired_stack_logs_in_then_calls_with_the_session_and_all_headers() -> None:
    server = ScriptedHttpServer().queue(
        ok_reply(token_payload("a")), ok_reply({"clientcode": "C123"})
    )
    stack = factory(
        server,
        settings(ANGELONE_CLIENT_PUBLIC_IP="203.0.113.7", ANGELONE_CLIENT_MAC_ADDRESS="aa:bb"),
    ).build()

    try:
        data = await stack.transport.send(RestRequest(Endpoints.PROFILE))
    finally:
        await stack.aclose()

    assert data == {"clientcode": "C123"}
    login, profile = server.requests
    body = json.loads(login.content)
    assert (body["clientcode"], body["password"]) == ("C123", "4321")
    assert body["totp"] in {
        pyotp.TOTP(SECRET).at(datetime.now(UTC).timestamp() + d) for d in (-30, 0, 30)
    }
    assert login.headers["X-PrivateKey"] == "the-api-key"
    assert "Authorization" not in login.headers
    assert profile.headers["Authorization"] == "Bearer jwt-a"
    assert profile.headers["X-ClientPublicIP"] == "203.0.113.7"
    assert profile.headers["X-MACAddress"] == "aa:bb"
    assert profile.headers["X-ClientLocalIP"] == "127.0.0.1"  # placeholder default


async def test_the_stack_shares_one_session_across_calls() -> None:
    server = ScriptedHttpServer().queue(ok_reply(token_payload("a")), ok_reply({}), ok_reply({}))
    stack = factory(server, settings()).build()

    try:
        await stack.transport.send(RestRequest(Endpoints.PROFILE))
        await stack.transport.send(RestRequest(Endpoints.FUNDS))
    finally:
        await stack.aclose()

    assert [r.url.path for r in server.requests].count(Endpoints.LOGIN.path) == 1


@pytest.mark.parametrize(
    "missing",
    ["ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE", "ANGELONE_PASSWORD", "ANGELONE_TOTP_SECRET"],
)
def test_missing_credentials_are_named_but_never_their_values(missing: str) -> None:
    config = settings(**{missing: ""})

    with pytest.raises(ConfigurationError) as raised:
        factory(ScriptedHttpServer(), config).build()

    assert missing in str(raised.value)
    for value in ("the-api-key", "C123", "4321", SECRET):
        assert value not in str(raised.value)
