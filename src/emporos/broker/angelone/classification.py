"""Maps every SmartAPI failure to exactly one typed, classified `BrokerError` (EM-43).

Phase 12's idempotency protocol depends on this being right: an outcome we cannot vouch for
(timeout, reset, 5xx, unparseable reply) is AMBIGUOUS and must never be read as success or
failure. Envelope-level failures are matched by an ordered, data-driven rule table so a new
error code is a one-line addition rather than an edit to the classifier (open/closed).

Codes marked "documented" come from SmartAPI's published error list and have not been seen
live; those marked "observed" were recorded from the live API. Text fragments back the codes
up because the gateway sometimes replies with a message and no code at all.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx

from emporos.broker.angelone.endpoints import Endpoint
from emporos.broker.angelone.envelope import Envelope
from emporos.broker.errors import (
    BrokerConnectionError,
    BrokerError,
    BrokerProtocolError,
    BrokerRateLimitedError,
    BrokerSessionExpiredError,
    BrokerTlsError,
    BrokerTransportError,
)
from emporos.core.errors import ErrorClassification

ErrorBuilder = Callable[[Envelope, Endpoint, int], BrokerError]


def _rate_limited(envelope: Envelope, endpoint: Endpoint, http_status: int) -> BrokerError:
    # The upstream limiter is known to fire spuriously (plan.md §1.4), so for a call that changes
    # broker state we cannot prove it was not processed: ambiguous. Reads are safely retryable.
    classification = (
        ErrorClassification.AMBIGUOUS if endpoint.mutates else ErrorClassification.RETRYABLE
    )
    return BrokerRateLimitedError(
        f"{endpoint.name}: rate limited ({envelope.message})",
        classification=classification,
        code=envelope.error_code or None,
        http_status=http_status,
    )


def _session_expired(envelope: Envelope, endpoint: Endpoint, http_status: int) -> BrokerError:
    return BrokerSessionExpiredError(
        f"{endpoint.name}: session rejected ({envelope.message})",
        code=envelope.error_code or None,
        http_status=http_status,
    )


def _server_fault(envelope: Envelope, endpoint: Endpoint, http_status: int) -> BrokerError:
    return BrokerTransportError(
        f"{endpoint.name}: broker-side fault ({envelope.message})",
        code=envelope.error_code or None,
        http_status=http_status,
    )


@dataclass(frozen=True)
class EnvelopeRule:
    """Matches a failed envelope by error code or message text (case-insensitive)."""

    build: ErrorBuilder
    codes: frozenset[str] = frozenset()
    fragments: tuple[str, ...] = ()

    def matches(self, envelope: Envelope) -> bool:
        if envelope.error_code in self.codes:
            return True
        message = envelope.message.lower()
        return any(fragment in message for fragment in self.fragments)


DEFAULT_ENVELOPE_RULES: tuple[EnvelopeRule, ...] = (
    # observed: text only, no code
    EnvelopeRule(_rate_limited, fragments=("exceeding access rate",)),
    # observed "Token missing" / "Invalid Token" (no code); documented AG8001-3, AB8050/1
    EnvelopeRule(
        _session_expired,
        codes=frozenset({"AG8001", "AG8002", "AG8003", "AB8050", "AB8051"}),
        fragments=("invalid token", "token missing", "token expired", "invalid refresh token"),
    ),
    # documented: AB1004 / AB2001 are the broker's transient internal-fault replies
    EnvelopeRule(
        _server_fault,
        codes=frozenset({"AB1004", "AB2001"}),
        fragments=("please try after sometime", "internal error"),
    ),
)


def _walk_causes(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


@dataclass(frozen=True)
class TransportRule:
    """Maps an httpx exception type to a broker error. First matching rule wins."""

    types: tuple[type[httpx.HTTPError], ...]
    build: Callable[[str], BrokerError]


DEFAULT_TRANSPORT_RULES: tuple[TransportRule, ...] = (
    # Never sent: no connection was made / obtained, so the action cannot have happened.
    TransportRule(
        (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout),
        lambda name: BrokerConnectionError(f"{name}: could not connect"),
    ),
    # Sent, no trustworthy answer: the broker may have acted.
    TransportRule(
        (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError),
        lambda name: BrokerTransportError(f"{name}: no reliable reply (timeout/reset)"),
    ),
    # Anything else httpx raises is treated the same way: assume the worst.
    TransportRule(
        (httpx.HTTPError,),
        lambda name: BrokerTransportError(f"{name}: unexpected transport failure"),
    ),
)


@dataclass(frozen=True)
class ErrorClassifier:
    envelope_rules: Sequence[EnvelopeRule] = field(default=DEFAULT_ENVELOPE_RULES)
    transport_rules: Sequence[TransportRule] = field(default=DEFAULT_TRANSPORT_RULES)

    def for_transport_failure(self, error: httpx.HTTPError, endpoint: Endpoint) -> BrokerError:
        """A request that raised inside httpx (connection, TLS, timeout, reset)."""
        if any(isinstance(cause, ssl.SSLError) for cause in _walk_causes(error)):
            return BrokerTlsError(f"{endpoint.name}: TLS verification/handshake failed")
        for rule in self.transport_rules:
            if isinstance(error, rule.types):
                return rule.build(endpoint.name)
        return BrokerTransportError(f"{endpoint.name}: unexpected transport failure")

    def for_response(
        self, status_code: int, envelope: Envelope | None, endpoint: Endpoint
    ) -> BrokerError | None:
        """Classify a received reply; `None` means it was a genuine success."""
        if envelope is not None and envelope.status is not True:
            for rule in self.envelope_rules:
                if rule.matches(envelope):
                    return rule.build(envelope, endpoint, status_code)

        if status_code >= 500:
            return BrokerTransportError(
                f"{endpoint.name}: HTTP {status_code}", http_status=status_code
            )
        if status_code == 429:
            rate = Envelope(status=False, message="HTTP 429", error_code="", data=None)
            return _rate_limited(rate, endpoint, status_code)
        if status_code == 401:
            return BrokerSessionExpiredError(f"{endpoint.name}: HTTP 401", http_status=status_code)
        if 400 <= status_code < 500:
            detail = f" ({envelope.message})" if envelope and envelope.message else ""
            return endpoint.rejection_error(
                f"{endpoint.name}: HTTP {status_code}{detail}",
                code=envelope.error_code or None if envelope else None,
                http_status=status_code,
            )
        if not 200 <= status_code < 300:
            return BrokerProtocolError(
                f"{endpoint.name}: unexpected HTTP {status_code}", http_status=status_code
            )

        if envelope is None:
            return BrokerProtocolError(
                f"{endpoint.name}: reply is not a SmartAPI envelope", http_status=status_code
            )
        if envelope.status is True:
            return None
        if envelope.status is False:
            return endpoint.rejection_error(
                f"{endpoint.name}: rejected ({envelope.message})",
                code=envelope.error_code or None,
                http_status=status_code,
            )
        return BrokerProtocolError(
            f"{endpoint.name}: envelope has no status ({envelope.message})",
            code=envelope.error_code or None,
            http_status=status_code,
        )
