"""Angel One's published rate limits per endpoint group (plan.md §1.4 **[VOLATILE]**).

Per-minute and per-hour caps are additional to the per-second caps, not derived from them.
Two deliberate departures from the raw table:

* `placeOrder` is documented at 20/s, but the static-IP rollout reduced it to 9/s and the plan
  designs for <= 5 orders/s, so 5/s is what we enforce.
* `getProfile` / `getRMS` / `logout` are not in the published table. They are grouped as
  "account" at the conservative 1/s that the sibling account endpoints (getPosition,
  getHolding) publish.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from emporos.broker.angelone.endpoints import EndpointGroup
from emporos.broker.ratelimit import RateLimit

ANGELONE_RATE_LIMITS: Mapping[str, RateLimit] = MappingProxyType(
    {
        EndpointGroup.LOGIN.value: RateLimit(per_second=1),
        EndpointGroup.ACCOUNT.value: RateLimit(per_second=1),
        EndpointGroup.PLACE_ORDER.value: RateLimit(per_second=5, per_minute=500, per_hour=1000),
        EndpointGroup.ORDER_BOOK.value: RateLimit(per_second=1),
        EndpointGroup.LTP.value: RateLimit(per_second=10, per_minute=500, per_hour=5000),
        EndpointGroup.POSITION.value: RateLimit(per_second=1),
        EndpointGroup.SEARCH_SCRIP.value: RateLimit(per_second=1),
        EndpointGroup.HOLDING.value: RateLimit(per_second=1),
        EndpointGroup.QUOTE.value: RateLimit(per_second=10, per_minute=500, per_hour=5000),
        EndpointGroup.CANDLES.value: RateLimit(per_second=3, per_minute=180, per_hour=5000),
    }
)
