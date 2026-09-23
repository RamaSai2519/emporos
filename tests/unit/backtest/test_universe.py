"""EM-177: dedicated coverage for `AsOfInstruments`/`InstrumentEra` — survivorship-safe universe
resolution as of a moment (previously only exercised indirectly through `test_job.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.domain.instruments import Exchange, Instrument, UnknownInstrumentError
from emporos.domain.money import Money

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 3, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)


def instrument(token: str, symbol: str) -> Instrument:
    return Instrument(Exchange.NSE, token, symbol, symbol, 1, Money.of("0.05"))


def test_an_era_must_end_after_it_starts() -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        InstrumentEra(instrument("1", "A-EQ"), T1, T0)


def test_an_open_ended_era_covers_every_moment_from_valid_from() -> None:
    era = InstrumentEra(instrument("1", "A-EQ"), T1, None)

    assert not era.covers(T0)
    assert era.covers(T1) and era.covers(T2)


def test_a_closed_era_covers_its_half_open_interval() -> None:
    era = InstrumentEra(instrument("1", "A-EQ"), T0, T1)

    assert era.covers(T0) and not era.covers(T1)  # valid_to is exclusive


def test_an_instrument_present_at_the_moment_is_in_the_resolved_universe() -> None:
    instruments = AsOfInstruments([InstrumentEra(instrument("1", "A-EQ"), T0, None)])

    resolved = instruments.as_of(T1)

    assert resolved.resolver.by_id("NSE:1").tradingsymbol == "A-EQ"
    assert resolved.assumed_ids == frozenset()


def test_an_instrument_with_no_era_covering_the_moment_is_absent_not_assumed() -> None:
    instruments = AsOfInstruments([InstrumentEra(instrument("1", "A-EQ"), T1, None)])

    resolved = instruments.as_of(T0)  # before the era starts

    with pytest.raises(UnknownInstrumentError):
        resolved.resolver.by_id("NSE:1")
    assert resolved.assumed_ids == frozenset()


def test_before_recorded_history_the_earliest_definition_is_used_only_when_opted_in() -> None:
    instruments = AsOfInstruments([InstrumentEra(instrument("1", "A-EQ"), T1, None)])

    default = instruments.as_of(T0, assume_earliest_before_history=False)
    with pytest.raises(UnknownInstrumentError):
        default.resolver.by_id("NSE:1")

    assumed = instruments.as_of(T0, assume_earliest_before_history=True)
    assert assumed.resolver.by_id("NSE:1").tradingsymbol == "A-EQ"
    assert assumed.assumed_ids == frozenset({"NSE:1"})


def test_a_symbol_rename_resolves_to_the_era_in_force_at_the_moment() -> None:
    """A superseded definition (a symbol/name change) sits alongside the current one; the era
    covering the requested moment wins, so a run sees the symbol the stock had THEN."""
    instruments = AsOfInstruments(
        [
            InstrumentEra(instrument("1", "OLD-EQ"), T0, T1),
            InstrumentEra(instrument("1", "NEW-EQ"), T1, None),
        ]
    )

    assert instruments.as_of(T0).resolver.by_id("NSE:1").tradingsymbol == "OLD-EQ"
    assert instruments.as_of(T2).resolver.by_id("NSE:1").tradingsymbol == "NEW-EQ"


def test_a_delisted_instrument_is_present_only_inside_its_closed_era() -> None:
    """A name later delisted still traded during its era: the universe as-of a past date must not
    drop it just because it is absent from today's master (survivorship, plan.md §10)."""
    instruments = AsOfInstruments([InstrumentEra(instrument("1", "DELISTED-EQ"), T0, T1)])

    assert instruments.as_of(T0).resolver.by_id("NSE:1").tradingsymbol == "DELISTED-EQ"
    with pytest.raises(UnknownInstrumentError):
        instruments.as_of(T2).resolver.by_id("NSE:1")


def test_each_call_resolves_independently_and_does_not_mutate_prior_results() -> None:
    instruments = AsOfInstruments(
        [
            InstrumentEra(instrument("1", "OLD-EQ"), T0, T1),
            InstrumentEra(instrument("1", "NEW-EQ"), T1, None),
        ]
    )

    early = instruments.as_of(T0)
    instruments.as_of(T2)  # a second, later resolution

    assert early.resolver.by_id("NSE:1").tradingsymbol == "OLD-EQ"  # unaffected by the later call
