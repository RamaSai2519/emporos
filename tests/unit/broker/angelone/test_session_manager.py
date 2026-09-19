"""EM-46: one live session, daily re-auth at midnight IST, cheap renewal, no login stampede."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.session import Credentials
from emporos.broker.angelone.session_manager import SessionManager
from emporos.broker.errors import BrokerAuthError, BrokerRejectedError, BrokerSessionExpiredError
from emporos.core.clock import IST, FixedClock
from tests.support.fakes import (
    FixedTotp,
    RecordingSessionStore,
    ScriptedRestTransport,
    token_payload,
)


class Rig:
    def __init__(self, start: datetime | None = None, **script: list[object]) -> None:
        self.clock = FixedClock(start or datetime(2026, 9, 21, 9, 15, tzinfo=IST))
        self.transport = ScriptedRestTransport(**script)
        self.store = RecordingSessionStore()
        auth = AngelOneAuthenticator(
            self.transport, Credentials("C1", "1111"), FixedTotp(), self.clock
        )
        self.manager = SessionManager(auth, self.store, self.clock)

    def logins(self) -> int:
        return len(self.transport.sent_to("loginByPassword"))

    def refreshes(self) -> int:
        return len(self.transport.sent_to("generateTokens"))


async def test_the_first_call_logs_in_and_later_calls_reuse_the_session() -> None:
    rig = Rig(loginByPassword=[token_payload("a")])

    sessions = [await rig.manager.session() for _ in range(3)]

    assert rig.logins() == 1
    assert sessions[0] is sessions[1] is sessions[2]


async def test_concurrent_callers_share_one_login() -> None:
    rig = Rig(loginByPassword=[token_payload("a")])

    results = await asyncio.gather(*(rig.manager.session() for _ in range(8)))

    assert rig.logins() == 1
    assert len({id(s) for s in results}) == 1


async def test_the_session_survives_until_midnight_then_reauthenticates_automatically() -> None:
    rig = Rig(
        datetime(2026, 9, 21, 22, 0, tzinfo=IST),
        loginByPassword=[token_payload("day1"), token_payload("day2")],
    )
    day1 = await rig.manager.session()

    rig.clock.set(datetime(2026, 9, 21, 23, 59, 59, tzinfo=IST))
    assert await rig.manager.session() is day1  # one second before midnight: still valid

    rig.clock.set(datetime(2026, 9, 22, 0, 0, 0, tzinfo=IST))  # the simulated midnight boundary
    day2 = await rig.manager.session()

    assert rig.logins() == 2
    assert day2.jwt == "jwt-day2"
    assert day2.expires_at == datetime(2026, 9, 23, 0, 0, tzinfo=IST)
    assert rig.store.load() is day2


async def test_a_session_left_idle_over_several_days_is_replaced_once() -> None:
    rig = Rig(loginByPassword=[token_payload("a"), token_payload("b")])
    await rig.manager.session()

    rig.clock.advance(timedelta(days=3))

    assert (await rig.manager.session()).jwt == "jwt-b"
    assert rig.logins() == 2


async def test_renewal_refreshes_tokens_without_a_full_login() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], generateTokens=[token_payload("b")])
    rejected = await rig.manager.session()

    renewed = await rig.manager.renew(rejected)

    assert (renewed.jwt, renewed.feed_token) == ("jwt-b", "feed-b")
    assert rig.logins() == 1 and rig.refreshes() == 1
    assert rig.store.load() is renewed


@pytest.mark.parametrize(
    "refusal", [BrokerSessionExpiredError("Invalid Refresh Token"), BrokerAuthError("no")]
)
async def test_renewal_falls_back_to_a_full_login_when_the_refresh_is_refused(
    refusal: Exception,
) -> None:
    rig = Rig(loginByPassword=[token_payload("a"), token_payload("c")], generateTokens=[refusal])
    rejected = await rig.manager.session()

    renewed = await rig.manager.renew(rejected)

    assert renewed.jwt == "jwt-c"
    assert rig.logins() == 2 and rig.refreshes() == 1


async def test_other_refresh_failures_are_not_papered_over_with_a_login() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], generateTokens=[BrokerRejectedError("weird")])
    rejected = await rig.manager.session()

    with pytest.raises(BrokerRejectedError):
        await rig.manager.renew(rejected)
    assert rig.logins() == 1


async def test_concurrent_renewals_of_the_same_rejected_session_refresh_once() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], generateTokens=[token_payload("b")])
    rejected = await rig.manager.session()

    results = await asyncio.gather(*(rig.manager.renew(rejected) for _ in range(5)))

    assert rig.refreshes() == 1
    assert {s.jwt for s in results} == {"jwt-b"}


async def test_feed_token_refresh_does_not_need_a_full_login() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], generateTokens=[token_payload("b")])

    assert await rig.manager.feed_token() == "feed-a"
    assert await rig.manager.refresh_feed_token() == "feed-b"

    assert rig.logins() == 1 and rig.refreshes() == 1


async def test_feed_token_refresh_falls_back_to_login_only_when_expired() -> None:
    rig = Rig(
        loginByPassword=[token_payload("a"), token_payload("c")],
        generateTokens=[BrokerSessionExpiredError("Refresh Token Expired")],
    )
    await rig.manager.session()

    assert await rig.manager.refresh_feed_token() == "feed-c"
    assert rig.logins() == 2


async def test_logout_ends_the_session_and_forgets_it() -> None:
    rig = Rig(loginByPassword=[token_payload("a"), token_payload("b")], logout=[None])
    await rig.manager.session()

    await rig.manager.logout()

    assert rig.store.clears == 1 and rig.store.load() is None
    assert len(rig.transport.sent_to("logout")) == 1
    assert (await rig.manager.session()).jwt == "jwt-b"  # next use logs in afresh


async def test_logout_forgets_the_session_even_if_the_broker_call_fails() -> None:
    rig = Rig(loginByPassword=[token_payload("a")], logout=[BrokerRejectedError("x")])
    await rig.manager.session()

    with pytest.raises(BrokerRejectedError):
        await rig.manager.logout()
    assert rig.store.load() is None


async def test_logout_with_no_session_is_a_no_op() -> None:
    rig = Rig()
    await rig.manager.logout()
    assert rig.transport.requests == []
