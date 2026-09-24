"""EM-191 §4.3: the one-shot vault. Sealed history cannot be read except through a committed unseal
record for one frozen candidate, and at most three of those can ever exist.

Styled after `test_look_ahead.py`: the guarantee is tested where it is enforced (the reader), with
a source that would happily hand the bars over, so a pass means the seal held, not that there was
nothing to see."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from emporos.backtest.vault import (
    UnsealRecord,
    VaultBurnedError,
    VaultedCandleReader,
    VaultGate,
    VaultSeal,
    VaultViolation,
)
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money

FIRST, LAST = date(2026, 3, 19), date(2026, 9, 18)
SEAL = VaultSeal(FIRST, LAST, None)
OPENED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
CANDIDATE = "cfg-abc+commit-123"


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST)


def unseal(
    number: int = 1,
    *,
    candidate: str = CANDIDATE,
    first: date = FIRST,
    last: date = LAST,
    instruments: frozenset[str] | None = None,
    seal: VaultSeal = SEAL,
) -> UnsealRecord:
    return UnsealRecord(
        number, seal.content_hash, candidate, "frozen candidate for S5", OPENED, first, last,
        instruments,
    )  # fmt: skip


class Source:
    """A candle source with data on every day: it would hand the vault over if asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, datetime, datetime]] = []

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.asked.append((instrument_id, start, end))
        price = Money.of(100)
        return [
            Candle(instrument_id, timeframe, start.astimezone(UTC), price, price, price, price, 1)
        ]


# --- the seal -------------------------------------------------------------------------------


async def test_a_read_inside_the_vault_is_refused_and_never_reaches_the_source() -> None:
    source = Source()
    reader = VaultedCandleReader(source, VaultGate(SEAL))

    with pytest.raises(VaultViolation, match="touches the vault"):
        await reader.get_range("NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5)))

    assert source.asked == []


async def test_a_read_that_only_overlaps_the_vault_is_refused_too() -> None:
    reader = VaultedCandleReader(Source(), VaultGate(SEAL))

    with pytest.raises(VaultViolation):  # starts before the vault, runs into its first day
        await reader.get_range("NSE:2885", Timeframe.D1, at(date(2026, 3, 1)), at(FIRST))
    with pytest.raises(VaultViolation):  # starts on its last day, runs past it
        await reader.get_range("NSE:2885", Timeframe.D1, at(LAST), at(date(2026, 9, 25)))


async def test_history_before_the_vault_reads_normally_up_to_its_first_instant() -> None:
    source = Source()
    reader = VaultedCandleReader(source, VaultGate(SEAL))

    start_of_vault = at(FIRST, hour=0)
    bars = await reader.get_range("NSE:2885", Timeframe.M5, at(date(2025, 1, 1)), start_of_vault)

    assert len(bars) == 1 and len(source.asked) == 1  # ends exactly where the vault begins


async def test_the_day_after_the_vault_is_not_sealed() -> None:
    reader = VaultedCandleReader(Source(), VaultGate(SEAL))

    bars = await reader.get_range(
        "NSE:2885", Timeframe.M5, at(date(2026, 9, 19), hour=0), at(date(2026, 9, 21))
    )

    assert len(bars) == 1


async def test_every_timeframe_is_sealed() -> None:
    reader = VaultedCandleReader(Source(), VaultGate(SEAL))

    for timeframe in Timeframe:
        with pytest.raises(VaultViolation):
            await reader.get_range(
                "NSE:2885", timeframe, at(date(2026, 6, 1)), at(date(2026, 6, 2))
            )


async def test_naming_a_candidate_without_a_record_opens_nothing() -> None:
    reader = VaultedCandleReader(Source(), VaultGate(SEAL), candidate_hash=CANDIDATE)

    with pytest.raises(VaultViolation, match="No committed unseal record"):
        await reader.get_range("NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5)))


# --- an unseal record admits exactly one candidate over exactly its range --------------------


async def test_a_record_admits_its_own_candidate_over_its_range() -> None:
    gate = VaultGate(SEAL, [unseal()])
    reader = VaultedCandleReader(Source(), gate, candidate_hash=CANDIDATE)

    bars = await reader.get_range(
        "NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5))
    )

    assert len(bars) == 1


async def test_a_record_does_not_admit_a_different_candidate() -> None:
    gate = VaultGate(SEAL, [unseal()])
    other = VaultedCandleReader(Source(), gate, candidate_hash="some-other-candidate")
    nameless = VaultedCandleReader(Source(), gate)

    for reader in (other, nameless):
        with pytest.raises(VaultViolation):
            await reader.get_range(
                "NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5))
            )


async def test_a_record_admits_only_the_days_it_names() -> None:
    gate = VaultGate(SEAL, [unseal(first=date(2026, 5, 1), last=date(2026, 5, 31))])
    reader = VaultedCandleReader(Source(), gate, candidate_hash=CANDIDATE)

    assert await reader.get_range(
        "NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 6))
    )
    with pytest.raises(VaultViolation):  # June is inside the vault but outside this open
        await reader.get_range("NSE:2885", Timeframe.M5, at(date(2026, 6, 1)), at(date(2026, 6, 2)))
    with pytest.raises(VaultViolation):  # so is a read that runs past the record's last day
        await reader.get_range(
            "NSE:2885", Timeframe.M5, at(date(2026, 5, 30)), at(date(2026, 6, 2))
        )


async def test_a_record_admits_only_its_candidates_instruments() -> None:
    gate = VaultGate(SEAL, [unseal(instruments=frozenset({"NSE:2885"}))])
    reader = VaultedCandleReader(Source(), gate, candidate_hash=CANDIDATE)

    assert await reader.get_range(
        "NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5))
    )
    with pytest.raises(VaultViolation):
        await reader.get_range(
            "NSE:11536", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5))
        )


async def test_warm_up_bars_from_before_the_vault_do_not_need_admitting() -> None:
    """A run over the vault reads a lookback of ordinary history first; only the sealed part of a
    read has to be covered by the record."""
    gate = VaultGate(SEAL, [unseal()])
    reader = VaultedCandleReader(Source(), gate, candidate_hash=CANDIDATE)

    bars = await reader.get_range(
        "NSE:2885", Timeframe.M5, at(date(2026, 2, 1)), at(date(2026, 4, 1))
    )

    assert len(bars) == 1


# --- an instrument-sealed vault only seals its instruments -----------------------------------


async def test_a_vault_sealed_by_instrument_leaves_the_others_open() -> None:
    seal = VaultSeal(FIRST, LAST, frozenset({"NSE:2885"}))
    reader = VaultedCandleReader(Source(), VaultGate(seal))

    assert await reader.get_range(
        "NSE:11536", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5))
    )
    with pytest.raises(VaultViolation):
        await reader.get_range("NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5)))


# --- at most three opens, ever ---------------------------------------------------------------


def test_the_fourth_open_cannot_exist() -> None:
    four = [unseal(n) for n in (1, 2, 3, 4)]

    with pytest.raises(VaultBurnedError, match="burned"):
        VaultGate(SEAL, four)


def test_three_opens_are_allowed_and_leave_none_left() -> None:
    gate = VaultGate(SEAL, [unseal(n) for n in (1, 2, 3)])

    assert (gate.opens_used, gate.opens_left) == (3, 0)


async def test_a_burned_vault_says_so_in_its_refusal() -> None:
    gate = VaultGate(SEAL, [unseal(n, candidate=f"c{n}") for n in (1, 2, 3)])
    reader = VaultedCandleReader(Source(), gate, candidate_hash="a-fourth-candidate")

    with pytest.raises(VaultViolation, match="burned"):
        await reader.get_range("NSE:2885", Timeframe.M5, at(date(2026, 5, 4)), at(date(2026, 5, 5)))


@pytest.mark.parametrize("numbers", [[2], [1, 3], [1, 1], [2, 3]])
def test_opens_are_numbered_from_one_without_gaps_or_repeats(numbers: list[int]) -> None:
    with pytest.raises(VaultBurnedError, match="no gap and no repeat"):
        VaultGate(SEAL, [unseal(n) for n in numbers])


def test_a_record_made_against_a_different_seal_does_not_carry_over() -> None:
    resealed = VaultSeal(FIRST, LAST, frozenset({"NSE:2885"}))  # what D1 would do

    with pytest.raises(VaultBurnedError, match="different seal"):
        VaultGate(resealed, [unseal(1, seal=SEAL)])


def test_a_record_cannot_admit_days_outside_the_vault() -> None:
    with pytest.raises(VaultBurnedError, match="outside the vault"):
        VaultGate(SEAL, [unseal(first=date(2026, 3, 1))])


def test_the_vault_can_never_be_given_more_than_three_opens() -> None:
    with pytest.raises(ValueError, match="1 to 3"):
        VaultSeal(FIRST, LAST, None, max_opens=4)


# --- the values themselves -------------------------------------------------------------------


def test_impossible_seals_and_records_are_refused() -> None:
    with pytest.raises(ValueError, match="after its last"):
        VaultSeal(LAST, FIRST, None)
    with pytest.raises(ValueError, match="cannot be empty"):
        VaultSeal(FIRST, LAST, frozenset())
    with pytest.raises(ValueError, match="numbered from 1"):
        unseal(0)
    with pytest.raises(ValueError, match="names its candidate"):
        unseal(candidate=" ")
    with pytest.raises(ValueError, match="timezone-aware"):
        UnsealRecord(1, "h", "c", "why", datetime(2026, 10, 1), FIRST, LAST, None)
    with pytest.raises(ValueError, match="after its last"):
        unseal(first=LAST, last=FIRST)


def test_the_seal_hash_follows_what_is_sealed() -> None:
    assert SEAL.content_hash == VaultSeal(FIRST, LAST, None).content_hash
    assert SEAL.content_hash != VaultSeal(FIRST, date(2026, 9, 17), None).content_hash
    assert SEAL.content_hash != VaultSeal(FIRST, LAST, frozenset({"NSE:1"})).content_hash
