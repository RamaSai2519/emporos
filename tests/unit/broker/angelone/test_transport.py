"""EM-43: the httpx transport — request shape, Decimal parsing, error mapping, TLS."""

from __future__ import annotations

import logging
import ssl
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.transport import (
    AngelOneHttpClientFactory,
    ClientIdentity,
    HttpRestTransport,
    RestRequest,
)
from emporos.broker.errors import (
    BrokerConnectionError,
    BrokerProtocolError,
    BrokerRejectedError,
    BrokerTlsError,
    BrokerTransportError,
)
from emporos.core.errors import ConfigurationError
from tests.support.fakes import ScriptedHttpServer, failed_reply, ok_reply
from tests.support.tls import SelfSignedTlsServer

API_KEY = "test-api-key-123"
BEARER = "test-bearer-token-456"
SRC = Path(__file__).resolve().parents[4] / "src" / "emporos"


def transport_for(server: ScriptedHttpServer) -> HttpRestTransport:
    return HttpRestTransport(server.client(), API_KEY)


async def test_sends_the_required_headers_and_bearer() -> None:
    server = ScriptedHttpServer().queue(ok_reply({"clientcode": "X"}))
    transport = HttpRestTransport(
        server.client(), API_KEY, ClientIdentity("10.0.0.5", "203.0.113.9", "aa:bb:cc:dd:ee:ff")
    )

    await transport.send(RestRequest(Endpoints.PROFILE, bearer=BEARER))

    request = server.requests[0]
    assert (request.method, request.url.path) == ("GET", Endpoints.PROFILE.path)
    assert request.headers["X-PrivateKey"] == API_KEY
    assert request.headers["Authorization"] == f"Bearer {BEARER}"
    assert request.headers["X-UserType"] == "USER"
    assert request.headers["X-SourceID"] == "WEB"
    assert request.headers["X-ClientLocalIP"] == "10.0.0.5"
    assert request.headers["X-ClientPublicIP"] == "203.0.113.9"
    assert request.headers["X-MACAddress"] == "aa:bb:cc:dd:ee:ff"


async def test_login_sends_a_json_body_and_no_authorization_header() -> None:
    server = ScriptedHttpServer().queue(ok_reply({"jwtToken": "j"}))

    await transport_for(server).send(RestRequest(Endpoints.LOGIN, body={"clientcode": "C"}))

    request = server.requests[0]
    assert request.method == "POST"
    assert b'"clientcode"' in request.content
    assert "Authorization" not in request.headers


async def test_prices_are_parsed_as_decimal_never_float() -> None:
    server = ScriptedHttpServer().queue(
        httpx.Response(
            200,
            content=b'{"status":true,"message":"SUCCESS","errorcode":"","data":'
            b'{"ltp":996.2,"open":0.1,"volume":5699456}}',
        )
    )

    data = await transport_for(server).send(RestRequest(Endpoints.LTP, body={}, bearer=BEARER))

    assert data["ltp"] == Decimal("996.2")
    assert isinstance(data["ltp"], Decimal)
    assert data["open"] == Decimal("0.1")  # 0.1 as a float would not round-trip exactly
    assert isinstance(data["volume"], int)


async def test_an_authenticated_endpoint_refuses_to_send_without_a_token() -> None:
    server = ScriptedHttpServer()
    with pytest.raises(ConfigurationError):
        await transport_for(server).send(RestRequest(Endpoints.PROFILE))
    assert server.requests == []


def test_an_empty_api_key_is_rejected_at_construction() -> None:
    with pytest.raises(ConfigurationError):
        HttpRestTransport(ScriptedHttpServer().client(), "")


async def test_a_business_failure_surfaces_as_a_typed_error_with_its_code() -> None:
    server = ScriptedHttpServer().queue(failed_reply("Order not found", "AB1013"))

    with pytest.raises(BrokerRejectedError) as raised:
        await transport_for(server).send(RestRequest(Endpoints.PROFILE, bearer=BEARER))

    assert raised.value.code == "AB1013"
    assert raised.value.http_status == 200


@pytest.mark.parametrize(
    ("scripted", "expected"),
    [
        (httpx.ConnectError("refused"), BrokerConnectionError),
        (httpx.ReadTimeout("slow"), BrokerTransportError),
        (httpx.ReadError("reset"), BrokerTransportError),
        (httpx.Response(502), BrokerTransportError),
        (httpx.Response(200, content=b"<html/>"), BrokerProtocolError),
    ],
)
async def test_transport_failures_are_typed(
    scripted: httpx.Response | Exception, expected: type[Exception]
) -> None:
    server = ScriptedHttpServer().queue(scripted)

    with pytest.raises(expected):
        await transport_for(server).send(RestRequest(Endpoints.PROFILE, bearer=BEARER))


async def test_secrets_never_reach_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    server = ScriptedHttpServer().queue(failed_reply("nope", "AB1"), httpx.ReadTimeout("t"))
    transport = transport_for(server)

    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            with pytest.raises(Exception) as raised:
                await transport.send(RestRequest(Endpoints.PROFILE, bearer=BEARER))
            assert API_KEY not in str(raised.value) and BEARER not in str(raised.value)

    assert API_KEY not in caplog.text and BEARER not in caplog.text
    assert BEARER not in repr(RestRequest(Endpoints.PROFILE, bearer=BEARER))


async def test_tls_verification_rejects_a_self_signed_certificate() -> None:
    """Behavioural proof that verification is on: an untrusted cert must be refused."""
    tls_server = SelfSignedTlsServer()
    async with tls_server.running() as url:
        client = AngelOneHttpClientFactory(url).create()
        transport = HttpRestTransport(client, API_KEY)
        try:
            with pytest.raises(BrokerTlsError) as raised:
                await transport.send(RestRequest(Endpoints.PROFILE, bearer=BEARER))
        finally:
            await client.aclose()

    assert raised.value.classification.value == "definitive"
    assert isinstance(raised.value.__cause__, httpx.ConnectError)
    assert tls_server.connections_completed == 0  # the handshake never completed, nothing was sent


async def test_the_client_factory_verifies_certificates_and_does_not_follow_redirects() -> None:
    client = AngelOneHttpClientFactory().create()
    try:
        context = client._transport._pool._ssl_context  # type: ignore[attr-defined]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert client.follow_redirects is False
    finally:
        await client.aclose()


def test_no_production_code_disables_tls_verification() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "verify=False" in path.read_text(encoding="utf-8")
        or "CERT_NONE" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
