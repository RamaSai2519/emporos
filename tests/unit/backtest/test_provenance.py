"""EM-177: `ProvenanceSnapshotter` — the dataset/universe/calendar/quarantine a run actually used,
content-hashed so two runs can be proven to share (or not share) the same data."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from emporos.backtest.provenance import ProvenanceSnapshotter
from emporos.backtest.universe import AsOfUniverse
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.history.quarantine import CorporateActionQuarantine, QuarantineEntry, QuarantineSource
from emporos.instruments.cache import InstrumentCache

MOMENT = datetime(2026, 3, 2, tzinfo=UTC)
DAY1, DAY2 = date(2026, 3, 2), date(2026, 3, 3)


def instrument(token: str) -> Instrument:
    return Instrument(Exchange.NSE, token, f"SYM{token}", f"SYM{token}", 1, Money.of("0.05"))


def universe(*tokens: str, assumed: frozenset[str] = frozenset()) -> AsOfUniverse:
    """Stands in for the whole instrument master the as-of resolver spans — deliberately wider
    than what any one run's strategy actually trades."""
    return AsOfUniverse(InstrumentCache([instrument(t) for t in tokens]), MOMENT, assumed)


def take(
    uni: AsOfUniverse,
    instrument_ids: Iterable[str],
    quarantine: CorporateActionQuarantine,
    calendar_version: str = "cal-1",
):  # type: ignore[no-untyped-def]
    return ProvenanceSnapshotter().take(
        uni, instrument_ids, Timeframe.M5, DAY1, DAY2, quarantine, calendar_version
    )


def test_the_same_inputs_hash_identically() -> None:
    uni = universe("1001", "1002")
    first = take(uni, ["NSE:1001", "NSE:1002"], CorporateActionQuarantine())
    second = take(uni, ["NSE:1002", "NSE:1001"], CorporateActionQuarantine())  # order irrelevant

    assert first.universe_hash == second.universe_hash
    assert first.quarantine_hash == second.quarantine_hash


def test_a_different_run_universe_hashes_differently() -> None:
    uni = universe("1001", "1002")
    smaller = take(uni, ["NSE:1001"], CorporateActionQuarantine())
    larger = take(uni, ["NSE:1001", "NSE:1002"], CorporateActionQuarantine())

    assert smaller.universe_hash != larger.universe_hash


def test_the_universe_hash_reflects_only_the_runs_own_instruments() -> None:
    """The as-of resolver can span the whole instrument master; the hash must not leak the rest of
    it into a two-symbol strategy's provenance."""
    wide = universe("1001", "1002", "9999")

    narrow_run = take(wide, ["NSE:1001", "NSE:1002"], CorporateActionQuarantine())
    equivalent_run = take(
        universe("1001", "1002"), ["NSE:1001", "NSE:1002"], CorporateActionQuarantine()
    )

    assert narrow_run.universe_hash == equivalent_run.universe_hash


def test_a_different_quarantine_state_hashes_differently() -> None:
    uni = universe("1001")
    empty = take(uni, ["NSE:1001"], CorporateActionQuarantine())
    entry = QuarantineEntry("NSE:1001", DAY1, "split", QuarantineSource.DETECTED, MOMENT)
    quarantined = take(uni, ["NSE:1001"], CorporateActionQuarantine([entry]))

    assert empty.quarantine_hash != quarantined.quarantine_hash


def test_a_different_calendar_version_is_carried_through_unhashed() -> None:
    uni = universe("1001")
    provenance = take(uni, ["NSE:1001"], CorporateActionQuarantine(), calendar_version="cal-xyz")

    assert provenance.calendar_version == "cal-xyz"


def test_assumed_instrument_ids_are_restricted_to_the_runs_own_universe() -> None:
    """`universe.assumed_ids` can span the whole master; only the ids THIS run actually uses (and
    which were assumed) belong in its provenance."""
    uni = universe("1001", "1002", "9999", assumed=frozenset({"NSE:1002", "NSE:9999"}))

    provenance = take(uni, ["NSE:1001", "NSE:1002"], CorporateActionQuarantine())

    assert provenance.assumed_instrument_ids == ("NSE:1002",)  # NSE:9999 is not this run's


def test_dataset_window_and_timeframe_are_recorded_verbatim() -> None:
    uni = universe("1001")
    provenance = take(uni, ["NSE:1001"], CorporateActionQuarantine())

    assert provenance.dataset_timeframe is Timeframe.M5
    assert provenance.dataset_first == DAY1 and provenance.dataset_last == DAY2
