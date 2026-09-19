"""EM-47: the transport layer against RECORDED SmartAPI fixtures (authoritative over the docs).

CI needs no credentials and no network: every fixture was captured once from the live API
(scripts/record_angelone_fixtures.py), scrubbed, and is replayed here through the real
transport, classifier and parser."""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from emporos.broker.angelone.api import AngelOneApi, CandleInterval, QuoteMode
from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.endpoints import Endpoint, Endpoints
from emporos.broker.angelone.session import Credentials, Session, SessionExpiry
from emporos.broker.angelone.transport import HttpRestTransport, RestRequest
from emporos.broker.errors import (
    BrokerAuthError,
    BrokerError,
    BrokerProtocolError,
    BrokerRateLimitedError,
    BrokerRejectedError,
    BrokerSessionExpiredError,
    BrokerTransportError,
)
from emporos.core.clock import IST, FixedClock
from emporos.core.errors import ErrorClassification
from tests.support.angelone_fixtures import FixtureLibrary, Recording
from tests.support.fakes import FixedTotp, ScriptedHttpServer

pytestmark = pytest.mark.contract

LIBRARY = FixtureLibrary()
RECORDED = LIBRARY.recorded()
SYNTHETIC = LIBRARY.synthetic()
NOW = datetime(2026, 9, 21, 9, 0, tzinfo=IST)


def wire(recording: Recording) -> tuple[ScriptedHttpServer, HttpRestTransport]:
    server = ScriptedHttpServer().queue(recording.response())
    return server, HttpRestTransport(server.client(), "test-api-key")


def api_for(recording: Recording) -> tuple[ScriptedHttpServer, AngelOneApi]:
    server, transport = wire(recording)
    return server, AngelOneApi(_AuthenticatedAs("scrubbed-jwt-token", transport))


class _AuthenticatedAs:
    """Supplies a fixed bearer, standing in for the session layer (covered elsewhere)."""

    def __init__(self, bearer: str, inner: HttpRestTransport) -> None:
        self._bearer = bearer
        self._inner = inner

    async def send(self, request: RestRequest) -> Any:
        return await self._inner.send(replace(request, bearer=request.bearer or self._bearer))


CANDLE_FROM, CANDLE_TO = (
    datetime(2026, 9, 18, 9, 15, tzinfo=IST),
    datetime(2026, 9, 18, 9, 19, tzinfo=IST),
)

# recording name -> the typed call that produced it
TYPED_CALLS: dict[str, Callable[[AngelOneApi], Awaitable[object]]] = {
    "profile": lambda api: api.profile(),
    "funds": lambda api: api.funds(),
    "ltp": lambda api: api.ltp("NSE", "SBIN-EQ", "3045"),
    "quote_full": lambda api: api.quotes(QuoteMode.FULL, {"NSE": ["3045"]}),
    "candles_1m": lambda api: api.candles(
        "NSE", "3045", CandleInterval.ONE_MINUTE, CANDLE_FROM, CANDLE_TO
    ),
}

# recording name -> the classified error a refusal must raise
REFUSALS: dict[str, type[BrokerError]] = {
    "login_bad_totp": BrokerAuthError,
    "login_rate_limited": BrokerRateLimitedError,
    "profile_invalid_token": BrokerSessionExpiredError,
    "profile_missing_token": BrokerSessionExpiredError,
    "candles_bad_interval": BrokerRejectedError,
}
AUTH_RECORDINGS = ("login_success", "generate_tokens", "logout")


def endpoint_for(recording: Recording) -> Endpoint:
    catalog = [v for v in vars(Endpoints).values() if isinstance(v, Endpoint)]
    (endpoint,) = (e for e in catalog if e.path == recording.path)
    return (
        endpoint
        if recording.header_names and "authorization" in recording.header_names
        else (replace(endpoint, authenticated=False))
    )


def sent_headers(request: httpx.Request) -> set[str]:
    return {h for h in request.headers if h.startswith("x-") or h == "authorization"}


def test_every_recorded_fixture_is_covered_by_a_contract_case() -> None:
    covered = set(TYPED_CALLS) | set(REFUSALS) | set(AUTH_RECORDINGS)
    assert covered == set(RECORDED), "add a contract case for every new fixture"


def test_recordings_are_scrubbed_and_declare_their_provenance() -> None:
    assert {r.provenance for r in RECORDED.values()} == {"recorded"}
    assert {r.provenance for r in SYNTHETIC.values()} == {"synthetic"}
    text = json.dumps([r.body for r in RECORDED.values()])
    assert "eyJ" not in text and "@example.invalid" in text


@pytest.mark.parametrize("name", sorted(TYPED_CALLS))
async def test_the_transport_parses_every_recorded_success(name: str) -> None:
    recording = RECORDED[name]
    server, api = api_for(recording)

    result = await TYPED_CALLS[name](api)

    assert result  # parsed into a typed, non-empty model
    (request,) = server.requests
    assert (request.method, request.url.path) == (recording.method, recording.path)
    body = json.loads(request.content) if request.content else None
    assert body == recording.request_body  # we send exactly what the live API was sent
    assert sent_headers(request) == set(recording.header_names)


async def test_recorded_profile_and_funds_parse_to_typed_values() -> None:
    _, api = api_for(RECORDED["profile"])
    profile = await api.profile()
    assert profile.client_code == "A0000000" and "nse_cm" in profile.exchanges
    assert "MIS" in profile.products

    _, api = api_for(RECORDED["funds"])
    funds = await api.funds()
    assert funds.net == Decimal("100000.00") and isinstance(funds.net, Decimal)
    assert funds.available_cash == Decimal("100000.00")


async def test_recorded_market_data_parses_to_exact_decimals_and_utc() -> None:
    _, api = api_for(RECORDED["ltp"])
    ltp = await api.ltp("NSE", "SBIN-EQ", "3045")
    assert (ltp.trading_symbol, ltp.symbol_token) == ("SBIN-EQ", "3045")
    assert isinstance(ltp.ltp, Decimal) and ltp.low <= ltp.ltp <= ltp.high

    _, api = api_for(RECORDED["quote_full"])
    quote = await api.quotes(QuoteMode.FULL, {"NSE": ["3045"]})
    (entry,) = quote.fetched
    assert entry.symbol_token == "3045" and entry.trade_volume > 0
    assert entry.lower_circuit < entry.ltp < entry.upper_circuit
    assert (
        entry.exch_trade_time.tzinfo is UTC
        or entry.exch_trade_time.utcoffset().total_seconds() == 0
    )  # type: ignore[union-attr]

    _, api = api_for(RECORDED["candles_1m"])
    bars = await api.candles("NSE", "3045", CandleInterval.ONE_MINUTE, CANDLE_FROM, CANDLE_TO)
    assert len(bars) == 5
    assert bars[0].ts == datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST
    assert [b.ts.minute for b in bars] == [45, 46, 47, 48, 49]
    assert all(b.low <= b.open <= b.high and b.low <= b.close <= b.high for b in bars)
    assert all(isinstance(b.open, Decimal) for b in bars)


@pytest.mark.parametrize("name", AUTH_RECORDINGS)
async def test_the_auth_calls_replay_against_their_recordings(name: str) -> None:
    recording = RECORDED[name]
    server, transport = wire(recording)
    auth = AngelOneAuthenticator(
        transport, Credentials("A0000000", "0000"), FixedTotp("000000"), FixedClock(NOW)
    )

    if name == "login_success":
        session = await auth.login()
        assert (session.jwt, session.feed_token) == ("scrubbed-jwt-token", "scrubbed-feed-token")
    else:
        session = _session_like()
        if name == "generate_tokens":
            assert (await auth.renew(session)).refresh_token == "scrubbed-refresh-token"
        else:
            await auth.logout(session)

    (request,) = server.requests
    assert (request.method, request.url.path) == (recording.method, recording.path)
    assert json.loads(request.content) == recording.request_body
    assert sent_headers(request) == set(recording.header_names)


def _session_like() -> Session:
    return Session(
        "scrubbed-jwt-token",
        "scrubbed-refresh-token",
        "scrubbed-feed-token",
        NOW,
        SessionExpiry().expires_at(NOW),
    )


@pytest.mark.parametrize("name", sorted(REFUSALS))
async def test_every_recorded_refusal_maps_to_its_classified_error(name: str) -> None:
    recording = RECORDED[name]
    _, transport = wire(recording)
    request = RestRequest(
        endpoint_for(recording),
        body=recording.request_body,
        bearer="x" if "authorization" in recording.header_names else None,
    )

    with pytest.raises(REFUSALS[name]) as raised:
        await transport.send(request)

    assert isinstance(raised.value.classification, ErrorClassification)
    assert raised.value.http_status == recording.status


def test_recorded_refusal_codes_are_kept_for_diagnostics() -> None:
    assert RECORDED["login_bad_totp"].body["errorcode"] == "AB1050"
    assert RECORDED["profile_invalid_token"].body["errorCode"] == "AG8001"
    assert RECORDED["profile_missing_token"].body["errorCode"] == "AG8003"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("rate_limit_defect", BrokerRateLimitedError),
        ("rate_limit_defect_gateway_dialect", BrokerRateLimitedError),
        ("http_502_html", BrokerTransportError),
        ("internal_error_ab2001", BrokerTransportError),
    ],
)
async def test_synthetic_failure_conditions_are_classified(
    name: str, expected: type[BrokerError]
) -> None:
    _, transport = wire(SYNTHETIC[name])
    with pytest.raises(expected):
        await transport.send(RestRequest(Endpoints.CANDLES, body={}, bearer="x"))


async def test_a_login_reply_without_all_tokens_is_rejected_as_a_protocol_fault() -> None:
    server, transport = wire(SYNTHETIC["login_success_missing_tokens"])
    auth = AngelOneAuthenticator(transport, Credentials("A", "1"), FixedTotp(), FixedClock(NOW))
    with pytest.raises(BrokerProtocolError):
        await auth.login()
    assert len(server.requests) == 1


# ---- deliberate corruption: every damaged fixture is a typed error, never a crash ----------


def _corruptions(recording: Recording) -> list[tuple[str, Recording]]:
    """Damaged variants of a success fixture: broken text, wrong containers, wrong leaf types,
    and every field removed in turn."""
    text = json.dumps(recording.body)
    variants: list[tuple[str, Recording]] = [
        ("truncated", replace(recording, body=None, body_text=text[: len(text) // 2])),
        ("html", replace(recording, body=None, body_text="<html>maintenance</html>")),
        ("empty", replace(recording, body=None, body_text="")),
        ("binary", replace(recording, body=None, body_text="��")),
    ]
    for label, data in (
        ("str", "oops"),
        ("null", None),
        ("int", 7),
        ("list", [1, 2]),
        ("dict", {}),
    ):
        variants.append((f"data_is_{label}", _with_data(recording, data)))
    data = recording.data
    if isinstance(data, dict):
        for key in data:
            dropped = {k: v for k, v in data.items() if k != key}
            variants.append((f"drop_{key}", _with_data(recording, dropped)))
        for key in data:
            for junk in ("not-a-number", None, [], {}):
                variants.append(
                    (
                        f"retype_{key}_{type(junk).__name__}",
                        _with_data(recording, {**data, key: junk}),
                    )
                )
    elif isinstance(data, list) and data:
        for column in range(len(data[0])):
            rows = [[v for i, v in enumerate(row) if i != column] for row in data]
            variants.append((f"drop_column_{column}", _with_data(recording, rows)))
            rows = [
                [("not-a-number" if i == column else v) for i, v in enumerate(row)] for row in data
            ]
            variants.append((f"retype_column_{column}", _with_data(recording, rows)))
    return variants


def _with_data(recording: Recording, data: object) -> Recording:
    body = copy.deepcopy(recording.body)
    body["data"] = data
    return replace(recording, body=body, body_text=None)


CORRUPTED = [
    pytest.param(name, label, variant, id=f"{name}:{label}")
    for name in sorted(TYPED_CALLS)
    for label, variant in _corruptions(RECORDED[name])
]


@pytest.mark.parametrize(("name", "label", "variant"), CORRUPTED)
async def test_a_corrupted_fixture_is_never_a_crash(
    name: str, label: str, variant: Recording
) -> None:
    """Either the damage is harmless (an optional field) and it still parses, or it is refused
    with a typed `BrokerError`. Any other exception type fails the test."""
    assert label  # the parametrize id; the label names the damage in the test report
    _, api = api_for(variant)
    with contextlib.suppress(BrokerError):
        await TYPED_CALLS[name](api)


@pytest.mark.parametrize("name", sorted(TYPED_CALLS))
@pytest.mark.parametrize(
    "label", ["truncated", "html", "empty", "binary", "data_is_str", "data_is_null"]
)
async def test_structurally_broken_fixtures_are_rejected_as_ambiguous_protocol_errors(
    name: str, label: str
) -> None:
    variant = dict(_corruptions(RECORDED[name]))[label]
    _, api = api_for(variant)

    with pytest.raises(BrokerError) as raised:
        await TYPED_CALLS[name](api)

    assert raised.value.classification.value == "ambiguous"
    assert isinstance(raised.value, BrokerProtocolError)


@pytest.mark.parametrize("name", ["profile", "funds", "ltp"])
async def test_removing_a_required_field_is_refused(name: str) -> None:
    data = RECORDED[name].data
    required = {"profile": "clientcode", "funds": "net", "ltp": "ltp"}[name]
    variant = _with_data(RECORDED[name], {k: v for k, v in data.items() if k != required})
    _, api = api_for(variant)

    with pytest.raises(BrokerProtocolError):
        await TYPED_CALLS[name](api)
