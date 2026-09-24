from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.jev.config import JevConfig
from emporos.jev.leakage import (
    CutoffEnforcingJevProvider,
    JevLeakageError,
    KnowledgeCutoffGuard,
    SymbolAnonymiser,
)
from tests.support.jev import RecordingProvider, make_jev_request

CUTOFF = date(2025, 1, 31)  # a test value; the real cutoff is always declared, never assumed
CONFIG = JevConfig(enabled=True, model_knowledge_cutoff=CUTOFF)
MARGIN = timedelta(days=10)


def test_a_missing_cutoff_is_a_refusal_not_a_default() -> None:
    with pytest.raises(JevLeakageError, match="no model knowledge cutoff is declared"):
        KnowledgeCutoffGuard().check(date(2030, 1, 1), JevConfig(enabled=True))


def test_a_window_starting_at_the_cutoff_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="may remember"):
        KnowledgeCutoffGuard(timedelta(0)).check(CUTOFF, CONFIG)


def test_a_window_starting_before_the_cutoff_is_refused() -> None:
    with pytest.raises(JevLeakageError):
        KnowledgeCutoffGuard(timedelta(0)).check(CUTOFF - timedelta(days=400), CONFIG)


def test_the_margin_extends_the_unsafe_period() -> None:
    guard = KnowledgeCutoffGuard(MARGIN)

    with pytest.raises(JevLeakageError):
        guard.check(CUTOFF + MARGIN, CONFIG)
    guard.check(CUTOFF + MARGIN + timedelta(days=1), CONFIG)


def test_the_earliest_allowed_day_is_the_first_safe_one() -> None:
    assert KnowledgeCutoffGuard(MARGIN).earliest_allowed(CONFIG) == CUTOFF + timedelta(days=11)


def test_a_negative_margin_is_refused() -> None:
    with pytest.raises(ValueError, match="margin"):
        KnowledgeCutoffGuard(timedelta(days=-1))


async def test_the_enforcing_provider_refuses_a_request_inside_the_margin() -> None:
    inner = RecordingProvider()
    provider = CutoffEnforcingJevProvider(inner, KnowledgeCutoffGuard(timedelta(days=400)), CONFIG)

    with pytest.raises(JevLeakageError):
        await provider.decide(make_jev_request())  # as_of 2026-01-05 is < cutoff + 400 days


async def test_the_enforcing_provider_passes_a_safe_request_through() -> None:
    inner = RecordingProvider()
    config = JevConfig(enabled=True, model_knowledge_cutoff=date(2025, 6, 1))
    provider = CutoffEnforcingJevProvider(inner, KnowledgeCutoffGuard(MARGIN), config)

    decision = await provider.decide(make_jev_request())

    assert decision.ok
    assert len(inner.requests) == 1


async def test_a_refused_request_never_reaches_the_inner_provider() -> None:
    inner = RecordingProvider()
    config = JevConfig(enabled=True, model_knowledge_cutoff=date(2026, 1, 1))
    provider = CutoffEnforcingJevProvider(inner, KnowledgeCutoffGuard(MARGIN), config)

    with pytest.raises(JevLeakageError):
        await provider.decide(make_jev_request())

    assert inner.requests == []


def test_pseudonyms_are_stable_for_a_salt_and_differ_across_salts() -> None:
    inner = RecordingProvider()
    a1 = SymbolAnonymiser(inner, salt="run-a")
    a2 = SymbolAnonymiser(inner, salt="run-a")
    b = SymbolAnonymiser(inner, salt="run-b")

    assert a1.pseudonym("NSE:RELIANCE-EQ") == a2.pseudonym("NSE:RELIANCE-EQ")
    assert a1.pseudonym("NSE:RELIANCE-EQ") != b.pseudonym("NSE:RELIANCE-EQ")
    assert a1.pseudonym("NSE:RELIANCE-EQ") != a1.pseudonym("NSE:TCS-EQ")
    assert a1.pseudonym("NSE:TCS-EQ").startswith("INSTR_")


def test_an_anonymiser_needs_a_salt() -> None:
    with pytest.raises(ValueError, match="salt"):
        SymbolAnonymiser(RecordingProvider(), salt="")


async def test_the_inner_provider_only_ever_sees_the_pseudonym() -> None:
    inner = RecordingProvider()
    anonymiser = SymbolAnonymiser(inner, salt="s")

    await anonymiser.decide(make_jev_request())

    seen = inner.requests[0]
    assert seen.symbol == anonymiser.pseudonym("NSE:RELIANCE-EQ")
    assert "RELIANCE" not in repr(seen.payload())
    assert seen.as_of == make_jev_request().as_of  # kept for guards and records


async def test_the_deny_list_is_stripped_from_every_free_form_mapping() -> None:
    inner = RecordingProvider()
    request = make_jev_request(
        features={"rsi": Decimal(70), "Symbol": Decimal(1), "timestamp": Decimal(2)},
        historical_conditional_performance={"win_rate": Decimal("0.5"), "isin": Decimal(3)},
        portfolio_context={"cash": Decimal(9), "name": Decimal(4)},
    )

    await SymbolAnonymiser(inner, salt="s").decide(request)

    seen = inner.requests[0]
    assert dict(seen.features) == {"rsi": Decimal(70)}
    assert dict(seen.historical_conditional_performance) == {"win_rate": Decimal("0.5")}
    assert dict(seen.portfolio_context) == {"cash": Decimal(9)}


async def test_a_feature_key_that_names_the_instrument_is_stripped() -> None:
    inner = RecordingProvider()
    request = make_jev_request(features={"reliance_beta": Decimal(1), "rsi": Decimal(50)})

    await SymbolAnonymiser(inner, salt="s").decide(request)

    assert dict(inner.requests[0].features) == {"rsi": Decimal(50)}


def test_an_instrument_name_hiding_in_another_field_is_caught() -> None:
    anonymiser = SymbolAnonymiser(RecordingProvider(), salt="s")

    with pytest.raises(JevLeakageError, match="survived anonymisation"):
        anonymiser.anonymise(make_jev_request(strategy_name="reliance_special"))


def test_a_short_ticker_does_not_trip_on_an_unrelated_word() -> None:
    anonymiser = SymbolAnonymiser(RecordingProvider(), salt="s")

    anonymiser.anonymise(make_jev_request(symbol="NSE:IT-EQ", strategy_name="momentum_v1"))


def test_a_numeric_instrument_id_does_not_trip_on_a_price_with_the_same_digits() -> None:
    anonymiser = SymbolAnonymiser(RecordingProvider(), salt="s")

    cleaned = anonymiser.anonymise(
        make_jev_request(symbol="NSE:1001", entry=Decimal("1001.5"), target=Decimal("1010"))
    )

    assert cleaned.symbol.startswith("INSTR_")
