"""The SmartAPI REST endpoints we call, and the rate-limit group each belongs to.

`mutates` is the safety-critical bit of metadata: a mutating endpoint (login rotates the
session, generateTokens rotates the refresh token, and Phase 7's order endpoints change
broker state) has an unknown outcome when its reply is lost, so nothing in the transport
stack may replay it on ambiguity. Read-only endpoints are side-effect free and may be.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from emporos.broker.errors import BrokerAuthError, BrokerError, BrokerRejectedError

BASE_URL = "https://apiconnect.angelone.in"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class EndpointGroup(StrEnum):
    """Endpoints that share one upstream rate limit (plan.md §1.4)."""

    LOGIN = "login"
    ACCOUNT = "account"
    PLACE_ORDER = "place_order"
    ORDER_BOOK = "order_book"
    LTP = "ltp"
    POSITION = "position"
    SEARCH_SCRIP = "search_scrip"
    HOLDING = "holding"
    QUOTE = "quote"
    CANDLES = "candles"


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: HttpMethod
    path: str
    group: EndpointGroup
    mutates: bool = False
    authenticated: bool = True
    # What a `status: false` reply means here (a failed login is an auth failure).
    rejection_error: type[BrokerError] = BrokerRejectedError


class Endpoints:
    """The catalog. Phase 7 adds the order endpoints beside these."""

    LOGIN = Endpoint(
        "loginByPassword",
        HttpMethod.POST,
        "/rest/auth/angelbroking/user/v1/loginByPassword",
        EndpointGroup.LOGIN,
        mutates=True,
        authenticated=False,
        rejection_error=BrokerAuthError,
    )
    GENERATE_TOKENS = Endpoint(
        "generateTokens",
        HttpMethod.POST,
        "/rest/auth/angelbroking/jwt/v1/generateTokens",
        EndpointGroup.LOGIN,
        mutates=True,
        rejection_error=BrokerAuthError,
    )
    LOGOUT = Endpoint(
        "logout",
        HttpMethod.POST,
        "/rest/secure/angelbroking/user/v1/logout",
        EndpointGroup.ACCOUNT,
        mutates=True,
    )
    PROFILE = Endpoint(
        "getProfile",
        HttpMethod.GET,
        "/rest/secure/angelbroking/user/v1/getProfile",
        EndpointGroup.ACCOUNT,
    )
    FUNDS = Endpoint(
        "getRMS",
        HttpMethod.GET,
        "/rest/secure/angelbroking/user/v1/getRMS",
        EndpointGroup.ACCOUNT,
    )
    LTP = Endpoint(
        "getLtpData",
        HttpMethod.POST,
        "/rest/secure/angelbroking/order/v1/getLtpData",
        EndpointGroup.LTP,
    )
    QUOTE = Endpoint(
        "quote",
        HttpMethod.POST,
        "/rest/secure/angelbroking/market/v1/quote/",
        EndpointGroup.QUOTE,
    )
    CANDLES = Endpoint(
        "getCandleData",
        HttpMethod.POST,
        "/rest/secure/angelbroking/historical/v1/getCandleData",
        EndpointGroup.CANDLES,
    )
