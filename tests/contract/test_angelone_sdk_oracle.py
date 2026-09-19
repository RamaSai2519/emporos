"""EM-47: cross-check our request shapes against the pinned SmartAPI SDK (Decision 4 oracle).

The SDK is unmaintained, unthrottled and (on its WebSocket) TLS-unverified, so it is never
imported by production code. Here it is only asked one question: "what would you send?" — and
our transport must agree on method, path and body. Where we deliberately differ, the test says
why."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from emporos.broker.angelone.api import AngelOneApi, CandleInterval, QuoteMode
from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.transport import HttpRestTransport
from tests.contract.test_angelone_rest_contract import CANDLE_FROM, CANDLE_TO, _AuthenticatedAs
from tests.support.angelone_fixtures import FixtureLibrary
from tests.support.fakes import ScriptedHttpServer, ok_reply
from tests.support.sdk_oracle import SdkRequest, SdkRequestRecorder

pytestmark = pytest.mark.contract

RECORDED = FixtureLibrary().recorded()
REPLIES = {
    "api.login": RECORDED["login_success"].body,
    "api.user.profile": RECORDED["profile"].body,
    "api.logout": RECORDED["logout"].body,
    "api.refresh": RECORDED["generate_tokens"].body,
    "api.token": RECORDED["generate_tokens"].body,
    "api.rms.limit": RECORDED["funds"].body,
    "api.ltp.data": RECORDED["ltp"].body,
    "api.market.data": RECORDED["quote_full"].body,
    "api.candle.data": RECORDED["candles_1m"].body,
    "api.order.place": {"status": True, "data": {"orderid": "1"}},
    "api.order.modify": {"status": True, "data": {"orderid": "1"}},
    "api.order.cancel": {"status": True, "data": {"orderid": "1"}},
    "api.order.book": RECORDED["order_book_empty"].body,
    "api.trade.book": RECORDED["trade_book_empty"].body,
    "api.position": RECORDED["positions_empty"].body,
    "api.holding": RECORDED["holdings_empty"].body,
}


@pytest.fixture
def oracle(tmp_path: Path) -> SdkRequestRecorder:
    return SdkRequestRecorder(REPLIES, tmp_path)


async def our_request(call: Any, **replies: object) -> tuple[Any, Any]:
    """Run one of OUR API calls over a scripted server; return (server, result)."""
    server = ScriptedHttpServer().queue(*(ok_reply(r) for r in replies.values()))
    api = AngelOneApi(_AuthenticatedAs("t", HttpRestTransport(server.client(), "k")))
    return server, await call(api)


def body_of(request: Any) -> Any:
    return json.loads(request.content) if request.content else None


def assert_same_endpoint(
    sdk: SdkRequest, ours: Any, *, sdk_omits_trailing_slash: bool = False
) -> None:
    assert sdk.method == ours.method
    ours_path = ours.url.path.rstrip("/") if sdk_omits_trailing_slash else ours.url.path
    assert sdk.path == ours_path


def test_the_sdk_loads_hermetically_and_is_the_pinned_version(oracle: SdkRequestRecorder) -> None:
    import importlib.metadata as md

    assert md.version("smartapi-python") == "1.5.5"
    assert oracle.sdk.disable_ssl is False  # its REST default verifies TLS (its WebSocket does not)


def test_login_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    oracle.sdk.generateSession("A0000000", "0000", "000000")
    sdk = oracle.requests[0]
    assert (sdk.method, sdk.path) == ("POST", Endpoints.LOGIN.path)
    assert dict(sdk.params) == RECORDED["login_success"].request_body


def test_logout_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    sdk = oracle.capture(lambda s: s.terminateSession("A0000000"))
    assert (sdk.method, sdk.path) == ("POST", Endpoints.LOGOUT.path)
    assert dict(sdk.params) == RECORDED["logout"].request_body


def test_token_refresh_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    sdk = oracle.capture(lambda s: s.generateToken("scrubbed-refresh-token"))
    assert (sdk.method, sdk.path) == ("POST", Endpoints.GENERATE_TOKENS.path)
    assert dict(sdk.params) == RECORDED["generate_tokens"].request_body


async def test_profile_request_matches_the_sdk_on_method_and_path(
    oracle: SdkRequestRecorder,
) -> None:
    sdk = oracle.capture(lambda s: s.getProfile("scrubbed-refresh-token"))
    server, _ = await our_request(lambda api: api.profile(), profile=RECORDED["profile"].data)
    assert_same_endpoint(sdk, server.requests[0])
    # Deliberate divergence: the SDK tacks the refresh token on as a query parameter of this GET.
    # The live API does not need it (recorded), and it would put a secret in a URL, so we omit it.
    assert body_of(server.requests[0]) is None and "refreshToken" in sdk.params


async def test_funds_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    sdk = oracle.capture(lambda s: s.rmsLimit())
    server, _ = await our_request(lambda api: api.funds(), funds=RECORDED["funds"].data)
    assert_same_endpoint(sdk, server.requests[0])


async def test_ltp_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    sdk = oracle.capture(lambda s: s.ltpData("NSE", "SBIN-EQ", "3045"))
    server, _ = await our_request(
        lambda api: api.ltp("NSE", "SBIN-EQ", "3045"), ltp=RECORDED["ltp"].data
    )
    assert_same_endpoint(sdk, server.requests[0])
    assert dict(sdk.params) == body_of(server.requests[0])


async def test_quote_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    sdk = oracle.capture(lambda s: s.getMarketData("FULL", {"NSE": ["3045"]}))
    server, _ = await our_request(
        lambda api: api.quotes(QuoteMode.FULL, {"NSE": ["3045"]}), quote=RECORDED["quote_full"].data
    )
    # The SDK's route has no trailing slash; ours has (that is the path the live API served when
    # recorded). Same resource either way — the comparison ignores the slash, and says so.
    assert_same_endpoint(sdk, server.requests[0], sdk_omits_trailing_slash=True)
    assert dict(sdk.params) == body_of(server.requests[0])


async def test_candle_request_matches_the_sdk(oracle: SdkRequestRecorder) -> None:
    ours_body = RECORDED["candles_1m"].request_body
    sdk = oracle.capture(lambda s: s.getCandleData(ours_body))
    server, _ = await our_request(
        lambda api: api.candles("NSE", "3045", CandleInterval.ONE_MINUTE, CANDLE_FROM, CANDLE_TO),
        candles=RECORDED["candles_1m"].data,
    )
    assert_same_endpoint(sdk, server.requests[0])
    assert dict(sdk.params) == body_of(server.requests[0])  # same keys, same interval/date formats


def test_every_header_the_sdk_sends_we_send_too(oracle: SdkRequestRecorder) -> None:
    sdk_headers = {h.lower() for h in oracle.sdk.requestHeaders() if h.lower().startswith("x-")}
    assert sdk_headers <= set(RECORDED["profile"].header_names)


def test_the_sdk_parses_the_recorded_login_the_same_way_we_do(oracle: SdkRequestRecorder) -> None:
    """The SDK's own response handling, run over the recorded reality, reaches the same tokens."""
    result = oracle.sdk.generateSession("A0000000", "0000", "000000")
    assert result["data"]["jwtToken"] == "Bearer scrubbed-jwt-token"
    assert oracle.sdk.refresh_token == "scrubbed-refresh-token"
    assert oracle.sdk.getfeedToken() == "scrubbed-feed-token"


def test_loading_the_sdk_leaves_no_files_in_the_repository() -> None:
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "logs").exists()


@pytest.mark.parametrize(
    ("endpoint", "call"),
    [
        (Endpoints.PLACE_ORDER, lambda s: s.placeOrder({"variety": "NORMAL"})),
        (Endpoints.MODIFY_ORDER, lambda s: s.modifyOrder({"variety": "NORMAL"})),
        (Endpoints.CANCEL_ORDER, lambda s: s.cancelOrder("1", "NORMAL")),
        (Endpoints.ORDER_BOOK, lambda s: s.orderBook()),
        (Endpoints.TRADE_BOOK, lambda s: s.tradeBook()),
        (Endpoints.POSITIONS, lambda s: s.position()),
        (Endpoints.HOLDINGS, lambda s: s.holding()),
    ],
    ids=lambda v: getattr(v, "name", None),
)
def test_every_order_and_account_endpoint_matches_the_sdks_route_and_method(
    oracle: SdkRequestRecorder, endpoint: Any, call: Any
) -> None:
    """The order paths have never been called live, so the SDK is the oracle for the routes."""
    sdk = oracle.capture(call)
    assert (sdk.method, sdk.path) == (endpoint.method.value, endpoint.path)


def test_the_cancel_body_matches_the_sdks_parameters(oracle: SdkRequestRecorder) -> None:
    from emporos.broker.angelone.mapping import OrderRequestMapper
    from emporos.broker.models import CancelOrderRequest
    from emporos.domain.orders import OrderType

    sdk = oracle.capture(lambda s: s.cancelOrder("201", "NORMAL"))
    ours = OrderRequestMapper.cancel(CancelOrderRequest("201", OrderType.LIMIT))
    assert dict(sdk.params) == ours  # variety + orderid: identical keys and values
