"""EM-46: session expiry, secret hygiene, TOTP."""

from __future__ import annotations

from datetime import UTC, datetime

import pyotp
import pytest

from emporos.broker.angelone.session import (
    Credentials,
    InMemorySessionStore,
    PyotpTotp,
    Session,
    SessionExpiry,
)
from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError

EXPIRY = SessionExpiry()


def session(established: datetime) -> Session:
    return Session(
        "JWT-SECRET", "REFRESH-SECRET", "FEED-SECRET", established, EXPIRY.expires_at(established)
    )


@pytest.mark.parametrize(
    ("established", "expected"),
    [
        (datetime(2026, 9, 21, 9, 15, tzinfo=IST), datetime(2026, 9, 22, 0, 0, tzinfo=IST)),
        (datetime(2026, 9, 21, 23, 59, 59, tzinfo=IST), datetime(2026, 9, 22, 0, 0, tzinfo=IST)),
        (datetime(2026, 9, 21, 0, 0, 0, tzinfo=IST), datetime(2026, 9, 22, 0, 0, tzinfo=IST)),
        # 19:00 UTC on the 21st is already 00:30 IST on the 22nd -> expires at the *next* midnight
        (datetime(2026, 9, 21, 19, 0, tzinfo=UTC), datetime(2026, 9, 23, 0, 0, tzinfo=IST)),
    ],
)
def test_a_session_expires_at_the_next_midnight_ist(
    established: datetime, expected: datetime
) -> None:
    assert EXPIRY.expires_at(established) == expected


def test_expiry_is_exact_at_the_midnight_boundary() -> None:
    s = session(datetime(2026, 9, 21, 9, 15, tzinfo=IST))
    assert not EXPIRY.is_expired(s, datetime(2026, 9, 21, 23, 59, 59, tzinfo=IST))
    assert EXPIRY.is_expired(s, datetime(2026, 9, 22, 0, 0, 0, tzinfo=IST))
    assert EXPIRY.is_expired(s, datetime(2026, 9, 21, 18, 30, tzinfo=UTC))  # same instant, in UTC


def test_tokens_and_pin_never_appear_in_repr() -> None:
    text = repr(session(datetime(2026, 9, 21, tzinfo=IST))) + repr(Credentials("A1", "1234"))
    for secret in ("JWT-SECRET", "REFRESH-SECRET", "FEED-SECRET", "1234"):
        assert secret not in text
    assert "A1" in text  # the client code is not a secret


@pytest.mark.parametrize(("client", "pin"), [("", "1"), ("A", "")])
def test_credentials_must_be_complete(client: str, pin: str) -> None:
    with pytest.raises(ConfigurationError):
        Credentials(client, pin)


def test_totp_matches_the_authenticator_algorithm() -> None:
    secret = pyotp.random_base32()
    code = PyotpTotp(secret).code()
    assert len(code) == 6 and code.isdigit()
    assert code in {pyotp.TOTP(secret).at(datetime.now(UTC).timestamp() + d) for d in (-30, 0, 30)}


def test_the_totp_secret_is_not_exposed_and_is_required() -> None:
    totp = PyotpTotp("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in repr(totp)
    with pytest.raises(ConfigurationError):
        PyotpTotp("")


def test_the_in_memory_store_round_trips_and_clears() -> None:
    store = InMemorySessionStore()
    assert store.load() is None
    s = session(datetime(2026, 9, 21, tzinfo=IST))
    store.save(s)
    assert store.load() is s
    store.clear()
    assert store.load() is None
