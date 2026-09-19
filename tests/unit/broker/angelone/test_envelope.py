"""The reply envelope decoder, including both live dialects (recorded EM-47)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.broker.angelone.envelope import EnvelopeDecoder

DECODER = EnvelopeDecoder()


def test_the_standard_dialect() -> None:
    envelope = DECODER.decode(b'{"status":true,"message":"SUCCESS","errorcode":"","data":{"a":1}}')
    assert envelope is not None
    assert (envelope.status, envelope.message, envelope.error_code) == (True, "SUCCESS", "")
    assert envelope.data == {"a": 1}


def test_the_gateway_dialect_uses_success_and_camel_case_error_code() -> None:
    body = b'{"success":false,"errorCode":"AG8001","message":"Invalid Token","data":""}'
    envelope = DECODER.decode(body)
    assert envelope is not None
    assert (envelope.status, envelope.error_code, envelope.message) == (
        False,
        "AG8001",
        "Invalid Token",
    )


def test_a_bare_message_has_an_unknown_status() -> None:
    envelope = DECODER.decode(b'{"message":"Token missing"}')
    assert envelope is not None and envelope.status is None and envelope.error_code == ""


def test_floats_become_decimal_and_ints_stay_int() -> None:
    envelope = DECODER.decode(b'{"status":true,"data":{"p":996.2,"q":5}}')
    assert envelope is not None
    assert envelope.data["p"] == Decimal("996.2") and isinstance(envelope.data["p"], Decimal)
    assert isinstance(envelope.data["q"], int)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"   ",
        b"[1,2]",
        b'"str"',
        b"\xff\xfe",
        b'{"status":NaN}',  # looks like JSON but is not valid: never mistaken for plain text
        b'{"a":Infinity}',
        b"\x00\x01 binary",
        b"x" * 500,  # too long to be a gateway one-liner
    ],
)
def test_anything_that_is_not_an_object_or_a_short_text_decodes_to_none(content: bytes) -> None:
    assert DECODER.decode(content) is None


def test_a_short_plain_text_body_is_a_status_less_text_envelope() -> None:
    """Recorded live: the rate-limit denial is HTTP 403 with this text, not JSON."""
    envelope = DECODER.decode(b"Access denied because of exceeding access rate\n")
    assert envelope is not None
    assert envelope.status is None and envelope.from_text
    assert envelope.message == "Access denied because of exceeding access rate"


def test_a_non_boolean_flag_is_unknown_not_truthy() -> None:
    envelope = DECODER.decode(b'{"status":"true","success":1}')
    assert envelope is not None and envelope.status is None
