"""EM-50: the subscription manager — caps, atomicity, and restoring the watchlist on reconnect."""

from __future__ import annotations

import pytest

from emporos.broker.errors import BrokerConnectionError
from emporos.core.errors import ConfigurationError, ErrorClassification
from emporos.domain.instruments import Exchange, Instrument
from emporos.marketdata.subscriptions import (
    SubscriptionLimitExceededError,
    SubscriptionLimits,
    SubscriptionManager,
)
from tests.support.fakes import RecordingSubscriptionTransport, make_instrument


def instruments(count: int, start: int = 1, exchange: Exchange = Exchange.NSE) -> list[Instrument]:
    return [make_instrument(str(start + n), exchange) for n in range(count)]


async def connected(
    limits: SubscriptionLimits | None = None,
) -> tuple[SubscriptionManager, RecordingSubscriptionTransport]:
    transport = RecordingSubscriptionTransport()
    manager = SubscriptionManager(transport, limits)
    await manager.on_connected()
    return manager, transport


async def test_subscriptions_within_the_cap_are_sent_and_tracked() -> None:
    manager, transport = await connected()

    await manager.subscribe(instruments(3))

    assert transport.calls == [("subscribe", ("NSE:1", "NSE:2", "NSE:3"))]
    assert manager.is_subscribed("NSE:2") and len(manager.active) == 3


async def test_the_default_cap_is_the_plan_s_200_not_the_api_ceiling_of_1000() -> None:
    manager, _ = await connected()
    assert manager.limit == 200 == SubscriptionLimits.DEFAULT_CAP
    assert SubscriptionLimits.API_CEILING == 1000


async def test_a_batch_that_would_exceed_the_cap_is_rejected_whole_with_a_clear_error() -> None:
    manager, transport = await connected(SubscriptionLimits(max_tokens=5))
    await manager.subscribe(instruments(4))
    transport.calls.clear()

    with pytest.raises(SubscriptionLimitExceededError) as raised:
        await manager.subscribe(instruments(2, start=100))  # 4 + 2 > 5

    assert transport.calls == []  # nothing was sent...
    assert len(manager.active) == 4  # ...and none of the batch was kept (no silent truncation)
    assert (raised.value.requested_new, raised.value.active, raised.value.limit) == (2, 4, 5)
    assert "limit is 5" in str(raised.value)
    assert raised.value.classification is ErrorClassification.DEFINITIVE


async def test_exactly_the_cap_is_allowed_and_one_more_is_not() -> None:
    manager, _ = await connected(SubscriptionLimits(max_tokens=200))

    await manager.subscribe(instruments(200))
    assert len(manager.active) == 200

    with pytest.raises(SubscriptionLimitExceededError):
        await manager.subscribe(instruments(1, start=1000))


async def test_the_documented_1000_ceiling_can_be_configured_but_never_exceeded() -> None:
    manager, _ = await connected(SubscriptionLimits(max_tokens=1000))
    await manager.subscribe(instruments(1000))
    with pytest.raises(SubscriptionLimitExceededError):
        await manager.subscribe(instruments(1, start=5000))

    for bad in (0, -1, 1001):
        with pytest.raises(ConfigurationError):
            SubscriptionLimits(max_tokens=bad)


async def test_resubscribing_an_active_instrument_is_idempotent_and_costs_no_capacity() -> None:
    manager, transport = await connected(SubscriptionLimits(max_tokens=3))
    await manager.subscribe(instruments(3))
    transport.calls.clear()

    await manager.subscribe(instruments(3))  # all already active, even though the cap is full

    assert transport.calls == [] and len(manager.active) == 3


async def test_duplicates_within_one_batch_count_once() -> None:
    manager, transport = await connected(SubscriptionLimits(max_tokens=2))

    await manager.subscribe([make_instrument("1"), make_instrument("1"), make_instrument("2")])

    assert transport.calls == [("subscribe", ("NSE:1", "NSE:2"))]


async def test_the_same_token_on_two_exchanges_is_two_subscriptions() -> None:
    manager, _ = await connected()
    await manager.subscribe(
        [make_instrument("500", Exchange.NSE), make_instrument("500", Exchange.BSE)]
    )
    assert {i.instrument_id for i in manager.active} == {"NSE:500", "BSE:500"}


async def test_unsubscribing_frees_capacity_and_unknown_instruments_are_ignored() -> None:
    manager, transport = await connected(SubscriptionLimits(max_tokens=2))
    await manager.subscribe(instruments(2))
    transport.calls.clear()

    await manager.unsubscribe([make_instrument("1"), make_instrument("999")])
    await manager.subscribe([make_instrument("3")])

    assert transport.calls == [("unsubscribe", ("NSE:1",)), ("subscribe", ("NSE:3",))]
    assert {i.instrument_id for i in manager.active} == {"NSE:2", "NSE:3"}


async def test_a_reconnect_resubscribes_the_full_active_watchlist() -> None:
    manager, transport = await connected()
    await manager.subscribe(instruments(3))
    await manager.unsubscribe([make_instrument("2")])
    transport.calls.clear()

    await manager.on_disconnected("connection closed")
    await manager.on_connected()

    assert transport.calls == [("subscribe", ("NSE:1", "NSE:3"))]  # exactly the live set, once


async def test_a_reconnect_with_an_empty_watchlist_sends_nothing() -> None:
    manager, transport = await connected()
    await manager.on_disconnected("x")
    await manager.on_connected()
    assert transport.calls == []


async def test_subscriptions_made_while_disconnected_are_sent_when_the_feed_returns() -> None:
    transport = RecordingSubscriptionTransport()
    manager = SubscriptionManager(transport)  # not connected yet

    await manager.subscribe(instruments(2))
    assert transport.calls == [] and len(manager.active) == 2  # desired, not yet sent

    await manager.on_connected()
    assert transport.calls == [("subscribe", ("NSE:1", "NSE:2"))]


async def test_a_failed_send_keeps_the_instrument_desired_so_the_next_connect_repairs_it() -> None:
    manager, transport = await connected()
    transport.fail_with = BrokerConnectionError("socket dropped mid-send")

    with pytest.raises(BrokerConnectionError):
        await manager.subscribe(instruments(2))
    assert len(manager.active) == 2  # still wanted

    transport.fail_with = None
    transport.calls.clear()
    await manager.on_disconnected("dropped")
    await manager.on_connected()
    assert transport.calls == [("subscribe", ("NSE:1", "NSE:2"))]
