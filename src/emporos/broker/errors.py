"""Typed, classified broker errors (plan.md §4, Decision 4, Decision 7).

Every failure that crosses the broker boundary is one of these, and each carries
an `ErrorClassification` that says whether the *action took effect*:

* RETRYABLE  — provably did not take effect (never sent, or rejected before processing).
* DEFINITIVE — took effect or was refused, and the answer is final.
* AMBIGUOUS  — unknown (timeout, reset, 5xx, garbled reply). Never blindly retried for an
  order; resolved by asking the broker (`find_orders_by_tag`).

Callers branch on `classification` / the subclass, never on message text. Nothing here
knows about a concrete broker — adapters translate their wire failures into these.
"""

from __future__ import annotations

from emporos.core.errors import EmporosError, ErrorClassification


class BrokerError(EmporosError):
    """Base for every broker failure. `code`/`http_status` are diagnostics, not control flow."""

    def __init__(
        self,
        message: str,
        *,
        classification: ErrorClassification | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message, classification=classification)
        self.code = code
        self.http_status = http_status


class BrokerConnectionError(BrokerError):
    """The connection could not be established, so the request never left this process."""

    classification = ErrorClassification.RETRYABLE


class BrokerTlsError(BrokerError):
    """The server's TLS certificate failed verification. Never retried, never bypassed."""

    classification = ErrorClassification.DEFINITIVE


class BrokerTransportError(BrokerError):
    """The request may have reached the broker but no trustworthy answer came back
    (timeout, connection reset, 5xx)."""

    classification = ErrorClassification.AMBIGUOUS


class BrokerProtocolError(BrokerError):
    """A reply arrived but is not in a shape we understand (bad JSON, missing fields).
    We cannot tell what the broker did, so it is ambiguous."""

    classification = ErrorClassification.AMBIGUOUS


class BrokerRateLimitedError(BrokerError):
    """The broker refused the request for exceeding an access rate.

    Classification is chosen by the adapter per endpoint: retryable for reads, ambiguous for
    anything that mutates broker state (the upstream limiter is known to be defective, so we
    cannot prove a mutating call was not processed)."""

    classification = ErrorClassification.RETRYABLE


class BrokerSessionExpiredError(BrokerError):
    """The session token was missing, invalid or expired. Rejected before execution, so it is
    retryable *after* re-authenticating."""

    classification = ErrorClassification.RETRYABLE


class BrokerAuthError(BrokerError):
    """Login failed (bad credentials/TOTP, blocked account). Fix the input; do not retry."""

    classification = ErrorClassification.DEFINITIVE


class BrokerRejectedError(BrokerError):
    """The broker understood the request and refused it. The refusal is final."""

    classification = ErrorClassification.DEFINITIVE


class BrokerRetriesExhaustedError(BrokerError):
    """Bounded retries ran out. Keeps the last error's classification and chains it as the cause,
    so a persistent defect surfaces as a real failure rather than looping forever."""

    def __init__(self, message: str, *, last_error: BrokerError, attempts: int) -> None:
        super().__init__(
            message,
            classification=last_error.classification,
            code=last_error.code,
            http_status=last_error.http_status,
        )
        self.last_error = last_error
        self.attempts = attempts
