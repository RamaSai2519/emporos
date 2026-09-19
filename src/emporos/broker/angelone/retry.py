"""Bounded retry with backoff for SmartAPI calls (EM-45).

SmartAPI's `getCandleData` and `getOrderBook` answer "Access denied because of exceeding access
rate" at rates ~900x below the published limit (plan.md §1.4, unacknowledged upstream). That is
noise, not a real breach, so it is absorbed here — callers never special-case it — but the retries
are bounded so a defect that persists abnormally long still surfaces as a real failure.

What may be retried is decided by the error's classification, never by message text:

* RETRYABLE  — the action did not happen: retried with backoff. (A rejected *session* is the one
  exception: it needs re-authentication, not waiting, so it is left to the session layer.)
* AMBIGUOUS  — the outcome is unknown. Retried only for read-only endpoints, where replaying
  cannot do harm. A mutating endpoint (login, token rotation, and later every order call) is
  NEVER replayed here — that is Decision 7; the caller resolves it by asking the broker.
* DEFINITIVE — never retried.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from emporos.broker.angelone.endpoints import Endpoint, EndpointGroup
from emporos.broker.angelone.transport import RestRequest, RestTransport
from emporos.broker.backoff import BackoffPolicy, JitterSource
from emporos.broker.errors import (
    BrokerError,
    BrokerRetriesExhaustedError,
    BrokerSessionExpiredError,
)
from emporos.core.clock import Sleeper
from emporos.core.errors import ErrorClassification

_LOG = logging.getLogger(__name__)

DEFAULT_POLICY = BackoffPolicy(base_delay=0.5, max_delay=8.0, max_attempts=4)

# The defective endpoints get a patient policy: ~90s of ceiling delay across 8 attempts, so a
# normal run of spurious denials is absorbed but a sustained outage is reported.
DEFECT_POLICY = BackoffPolicy(base_delay=1.0, max_delay=30.0, max_attempts=8)

ANGELONE_RETRY_POLICIES: Mapping[str, BackoffPolicy] = MappingProxyType(
    {
        EndpointGroup.CANDLES.value: DEFECT_POLICY,
        EndpointGroup.ORDER_BOOK.value: DEFECT_POLICY,
    }
)


class RetryDecider:
    """Whether a failed attempt may be replayed. Pure: depends only on class and endpoint."""

    def may_retry(self, error: BrokerError, endpoint: Endpoint) -> bool:
        if isinstance(error, BrokerSessionExpiredError):
            return False
        if error.classification is ErrorClassification.RETRYABLE:
            return True
        if error.classification is ErrorClassification.AMBIGUOUS:
            return not endpoint.mutates
        return False


class RetryingTransport:
    def __init__(
        self,
        inner: RestTransport,
        sleeper: Sleeper,
        jitter: JitterSource,
        policies: Mapping[str, BackoffPolicy] = ANGELONE_RETRY_POLICIES,
        default_policy: BackoffPolicy = DEFAULT_POLICY,
        decider: RetryDecider | None = None,
    ) -> None:
        self._inner = inner
        self._sleeper = sleeper
        self._jitter = jitter
        self._policies = policies
        self._default_policy = default_policy
        self._decider = decider or RetryDecider()

    async def send(self, request: RestRequest) -> Any:
        endpoint = request.endpoint
        policy = self._policies.get(endpoint.group.value, self._default_policy)
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._inner.send(request)
            except BrokerError as error:
                if not self._decider.may_retry(error, endpoint):
                    raise
                if attempt >= policy.max_attempts:
                    _LOG.error("%s: giving up after %d attempts", endpoint.name, attempt)
                    raise BrokerRetriesExhaustedError(
                        f"{endpoint.name}: still failing after {attempt} attempts "
                        f"({type(error).__name__})",
                        last_error=error,
                        attempts=attempt,
                    ) from error
                delay = policy.delay(attempt - 1, self._jitter)
                _LOG.warning(
                    "%s: attempt %d failed (%s); retrying in %.2fs",
                    endpoint.name,
                    attempt,
                    type(error).__name__,
                    delay,
                )
                await self._sleeper.sleep(delay)
