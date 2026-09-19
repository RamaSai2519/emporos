"""Live Angel One checks (EM-46): a real TOTP login, renewal and logout.

Skipped unless ANGELONE_* credentials are set. NEVER prints or asserts on token values.
Places no orders — the dev key has no static IP registered, and only order APIs are IP-gated.

CAUTION: Angel One allows ONE session per client code. Running this while a production worker
is logged in as the same client would invalidate the worker's session.
"""

from __future__ import annotations

import pytest

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.factory import AngelOneStack
from emporos.broker.angelone.transport import RestRequest

pytestmark = pytest.mark.integration


async def test_totp_login_renewal_and_authenticated_calls(angelone_stack: AngelOneStack) -> None:
    stack = angelone_stack
    first = await stack.sessions.session()
    has_all_tokens = bool(first.jwt and first.refresh_token and first.feed_token)
    assert has_all_tokens
    assert first.expires_at > first.established_at

    reused = await stack.sessions.session() is first  # cached, no second login
    assert reused

    # Booleans only: a failing assert must never echo an account payload into logs or CI output.
    profile = await stack.transport.send(RestRequest(Endpoints.PROFILE))
    trades_nse_cash = isinstance(profile, dict) and "nse_cm" in profile.get("exchanges", [])
    assert trades_nse_cash

    # mid-day renewal via generateTokens; the midnight expiry is unchanged. (Tokens rotate only
    # if the renewal lands in a later second than the login — the JWT is timestamp-derived — so
    # rotation is deliberately not asserted.)
    renewed = await stack.sessions.renew(first)
    assert renewed.expires_at == first.expires_at

    # the renewed session works, and a fresh feed token needs no full login
    funds_returned = bool(await stack.transport.send(RestRequest(Endpoints.FUNDS)))
    feed_token_refreshed = bool(await stack.sessions.refresh_feed_token())
    assert funds_returned
    assert feed_token_refreshed

    await stack.sessions.logout()
