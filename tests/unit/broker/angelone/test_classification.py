"""EM-43: every documented/observed failure maps to exactly one of the three classes."""

from __future__ import annotations

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporos.broker.angelone.classification import ErrorClassifier
from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.envelope import Envelope, EnvelopeDecoder
from emporos.broker.errors import (
    BrokerAuthError,
    BrokerConnectionError,
    BrokerError,
    BrokerProtocolError,
    BrokerRateLimitedError,
    BrokerRejectedError,
    BrokerSessionExpiredError,
    BrokerTransportError,
)
from emporos.core.errors import ErrorClassification as C

CLASSIFIER = ErrorClassifier()
DECODER = EnvelopeDecoder()


def envelope(status: bool | None, message: str = "", code: str = "") -> Envelope:
    return Envelope(status=status, message=message, error_code=code, data=None)


@pytest.mark.parametrize(
    ("error", "expected_type", "expected_class"),
    [
        (httpx.ConnectTimeout("t"), BrokerConnectionError, C.RETRYABLE),
        (httpx.ConnectError("refused"), BrokerConnectionError, C.RETRYABLE),
        (httpx.PoolTimeout("no free connection"), BrokerConnectionError, C.RETRYABLE),
        (httpx.ReadTimeout("t"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.WriteTimeout("t"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.ReadError("reset"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.WriteError("reset"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.RemoteProtocolError("bad"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.LocalProtocolError("bad"), BrokerTransportError, C.AMBIGUOUS),
        (httpx.UnsupportedProtocol("x"), BrokerTransportError, C.AMBIGUOUS),
    ],
)
def test_transport_failures(
    error: httpx.HTTPError, expected_type: type[BrokerError], expected_class: C
) -> None:
    failure = CLASSIFIER.for_transport_failure(error, Endpoints.PROFILE)
    assert type(failure) is expected_type
    assert failure.classification is expected_class


@pytest.mark.parametrize(
    ("status", "body", "endpoint", "expected_type", "expected_class"),
    [
        # observed live: business failures come back as HTTP 200 + status false
        (
            200,
            b'{"status":false,"message":"Invalid totp","errorcode":"AB1050"}',
            Endpoints.LOGIN,
            BrokerAuthError,
            C.DEFINITIVE,
        ),
        (
            200,
            b'{"status":false,"message":"Some order problem","errorcode":"AB1013"}',
            Endpoints.PROFILE,
            BrokerRejectedError,
            C.DEFINITIVE,
        ),
        # observed live: gateway auth failures carry a message and no status
        (
            200,
            b'{"message":"Invalid Token","errorcode":""}',
            Endpoints.PROFILE,
            BrokerSessionExpiredError,
            C.RETRYABLE,
        ),
        (
            200,
            b'{"message":"Token missing"}',
            Endpoints.PROFILE,
            BrokerSessionExpiredError,
            C.RETRYABLE,
        ),
        (
            200,
            b'{"status":false,"message":"Token Expired","errorcode":"AG8002"}',
            Endpoints.LTP,
            BrokerSessionExpiredError,
            C.RETRYABLE,
        ),
        (
            200,
            b'{"status":false,"message":"x","errorcode":"AB8050"}',
            Endpoints.GENERATE_TOKENS,
            BrokerSessionExpiredError,
            C.RETRYABLE,
        ),
        # the rate-limit defect: retryable on reads, ambiguous on anything that mutates
        (
            200,
            b'{"status":false,"message":"Access denied because of exceeding access rate"}',
            Endpoints.CANDLES,
            BrokerRateLimitedError,
            C.RETRYABLE,
        ),
        (
            200,
            b'{"message":"Access denied because of exceeding access rate","errorcode":""}',
            Endpoints.CANDLES,
            BrokerRateLimitedError,
            C.RETRYABLE,
        ),
        (
            200,
            b'{"status":false,"message":"Access denied because of exceeding access rate"}',
            Endpoints.GENERATE_TOKENS,
            BrokerRateLimitedError,
            C.AMBIGUOUS,
        ),
        (429, b"", Endpoints.LTP, BrokerRateLimitedError, C.RETRYABLE),
        (429, b"", Endpoints.LOGIN, BrokerRateLimitedError, C.AMBIGUOUS),
        # transient broker-side faults: the outcome is unknown
        (
            200,
            b'{"status":false,"message":"Internal error, try after sometime","errorcode":"AB2001"}',
            Endpoints.PROFILE,
            BrokerTransportError,
            C.AMBIGUOUS,
        ),
        (500, b"", Endpoints.PROFILE, BrokerTransportError, C.AMBIGUOUS),
        (502, b"<html>bad gateway</html>", Endpoints.LTP, BrokerTransportError, C.AMBIGUOUS),
        (503, b"", Endpoints.LOGIN, BrokerTransportError, C.AMBIGUOUS),
        # HTTP-level refusals
        (401, b"", Endpoints.PROFILE, BrokerSessionExpiredError, C.RETRYABLE),
        (403, b"", Endpoints.PROFILE, BrokerRejectedError, C.DEFINITIVE),
        (400, b"", Endpoints.CANDLES, BrokerRejectedError, C.DEFINITIVE),  # observed: bad interval
        # replies we cannot make sense of
        (200, b"", Endpoints.PROFILE, BrokerProtocolError, C.AMBIGUOUS),
        (200, b"<html>maintenance</html>", Endpoints.PROFILE, BrokerProtocolError, C.AMBIGUOUS),
        (200, b"[1,2,3]", Endpoints.PROFILE, BrokerProtocolError, C.AMBIGUOUS),
        (200, b'{"message":"something new"}', Endpoints.PROFILE, BrokerProtocolError, C.AMBIGUOUS),
        (302, b"", Endpoints.PROFILE, BrokerProtocolError, C.AMBIGUOUS),
    ],
)
def test_responses(
    status: int,
    body: bytes,
    endpoint: object,
    expected_type: type[BrokerError],
    expected_class: C,
) -> None:
    failure = CLASSIFIER.for_response(status, DECODER.decode(body), endpoint)  # type: ignore[arg-type]
    assert failure is not None
    assert type(failure) is expected_type
    assert failure.classification is expected_class


def test_a_genuine_success_is_not_an_error() -> None:
    body = b'{"status":true,"message":"SUCCESS","errorcode":"","data":{"a":1}}'
    assert CLASSIFIER.for_response(200, DECODER.decode(body), Endpoints.PROFILE) is None


def test_a_success_envelope_is_not_mistaken_for_a_rule_match() -> None:
    # a successful reply whose text happens to contain a rule fragment is still a success
    body = b'{"status":true,"message":"invalid token is a phrase","errorcode":"","data":1}'
    assert CLASSIFIER.for_response(200, DECODER.decode(body), Endpoints.PROFILE) is None


def test_error_messages_never_echo_the_response_body() -> None:
    body = b"secret-body-content-xyz"
    failure = CLASSIFIER.for_response(500, DECODER.decode(body), Endpoints.PROFILE)
    assert failure is not None
    assert "secret-body-content-xyz" not in str(failure)


def test_every_endpoint_is_classified_the_same_shape() -> None:
    every = [v for k, v in vars(Endpoints).items() if not k.startswith("_")]
    assert every, "endpoint catalog is empty"
    for endpoint in every:
        failure = CLASSIFIER.for_response(500, None, endpoint)
        assert failure is not None
        assert failure.classification in set(C)


@given(
    status=st.integers(min_value=100, max_value=599),
    body=st.one_of(
        st.binary(max_size=64),
        st.builds(
            lambda s, m, c: (
                b'{"status":' + s + b',"message":"' + m + b'","errorcode":"' + c + b'"}'
            ),
            st.sampled_from([b"true", b"false", b"null", b'"x"']),
            st.text(alphabet="abc xyz", max_size=12).map(str.encode),
            st.text(alphabet="AB0123", max_size=6).map(str.encode),
        ),
    ),
)
def test_no_reply_escapes_classification(status: int, body: bytes) -> None:
    """Whatever the broker sends, we either call it a success or raise a classified error."""
    failure = CLASSIFIER.for_response(status, DECODER.decode(body), Endpoints.PROFILE)
    if failure is None:
        envelope = DECODER.decode(body)
        assert 200 <= status < 300
        assert envelope is not None
        assert envelope.status is True
    else:
        assert failure.classification in set(C)
