from decimal import Decimal

import pytest

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.instruments.downloader import CashSegmentFilter
from emporos.instruments.errors import MasterRejectedError
from emporos.instruments.validator import (
    InstrumentMasterValidator,
    RowParser,
    ValidationPolicy,
)
from tests.support.instrument_rows import as_master, cash_rows, sample_rows

LENIENT = ValidationPolicy(max_invalid_fraction=Decimal("0.10"), min_rows=5)


def _cash(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    accepts = CashSegmentFilter().accepts
    return [row for row in rows if accepts(row)]


def test_a_valid_master_passes_and_tick_sizes_are_converted_from_paise_to_rupees() -> None:
    validated = InstrumentMasterValidator(ValidationPolicy(min_rows=1000)).validate(
        as_master(cash_rows(1200)), current_count=1200
    )

    assert len(validated.instruments) == 1200
    assert validated.dropped_rows == 0
    assert validated.instruments[0].tick_size == Money.of("0.05")
    assert validated.instruments[0].instrument_id == "NSE:1000"


def test_the_recorded_sample_passes_and_drops_only_its_two_unusable_rows() -> None:
    master = as_master(_cash(sample_rows()))

    validated = InstrumentMasterValidator(LENIENT).validate(master, current_count=0)

    assert len(validated.instruments) == 24
    assert validated.dropped_rows == 2
    assert {i.exchange for i in validated.instruments} == {Exchange.NSE, Exchange.BSE}


def test_the_default_policy_tolerates_a_handful_of_unusable_rows_in_a_full_file() -> None:
    rows = cash_rows(2000)
    rows[0]["tick_size"] = "0.000000"  # like the real index-like cash rows
    rows[1]["lotsize"] = "-1"

    validated = InstrumentMasterValidator().validate(as_master(rows), current_count=2000)

    assert len(validated.instruments) == 1998
    assert validated.dropped_rows == 2


def test_a_truncated_file_is_rejected_for_too_few_rows() -> None:
    with pytest.raises(MasterRejectedError, match="only 300 cash rows"):
        InstrumentMasterValidator().validate(as_master(cash_rows(300)), current_count=0)


@pytest.mark.parametrize("size", [1500, 2500])
def test_a_row_count_more_than_twenty_percent_off_yesterday_is_rejected(size: int) -> None:
    with pytest.raises(MasterRejectedError, match=r"away from the current 2000"):
        InstrumentMasterValidator().validate(as_master(cash_rows(size)), current_count=2000)


@pytest.mark.parametrize("size", [1600, 2400])
def test_a_row_count_at_the_twenty_percent_edge_is_accepted(size: int) -> None:
    validated = InstrumentMasterValidator().validate(as_master(cash_rows(size)), current_count=2000)

    assert len(validated.instruments) == size


def test_the_row_count_check_is_skipped_on_the_first_ever_sync() -> None:
    assert InstrumentMasterValidator().validate(as_master(cash_rows(1000)), current_count=0)


def test_too_many_unusable_rows_reject_the_file_with_a_breakdown() -> None:
    rows = cash_rows(1500)
    for row in rows[:300]:
        del row["tick_size"]

    with pytest.raises(MasterRejectedError, match=r"300 of 1500 rows are unusable.*tick_size"):
        InstrumentMasterValidator().validate(as_master(rows), current_count=1500)


def test_a_schema_change_that_renames_a_required_column_is_rejected() -> None:
    rows = cash_rows(1200)
    for row in rows:
        row["lot_size"] = row.pop("lotsize")

    with pytest.raises(MasterRejectedError, match="missing field lotsize"):
        InstrumentMasterValidator().validate(as_master(rows), current_count=1200)


def test_duplicate_exchange_token_keys_are_rejected() -> None:
    rows = cash_rows(1200)
    rows[5]["token"] = rows[6]["token"]

    with pytest.raises(MasterRejectedError, match="duplicated"):
        InstrumentMasterValidator().validate(as_master(rows), current_count=1200)


def test_every_reason_is_reported_together() -> None:
    with pytest.raises(MasterRejectedError) as raised:
        InstrumentMasterValidator().validate(as_master(cash_rows(300)), current_count=2000)

    assert len(raised.value.reasons) == 2
    assert "only 300 cash rows" in str(raised.value)


def test_an_empty_master_is_rejected() -> None:
    with pytest.raises(MasterRejectedError, match="only 0 cash rows"):
        InstrumentMasterValidator().validate(as_master([]), current_count=0)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"tick_size": "abc"}, "unparseable"),
        ({"lotsize": "1.5"}, "unparseable"),
        ({"exch_seg": "NFO"}, "unparseable"),
        ({"lotsize": "0"}, "lot size"),
        ({"tick_size": "0.000000"}, "tick size"),
        ({"token": ""}, "missing field token"),
    ],
)
def test_the_row_parser_explains_why_a_row_is_unusable(
    override: dict[str, str], reason: str
) -> None:
    row = {**cash_rows(1)[0], **override}

    with pytest.raises(ValueError, match=reason):
        RowParser().parse(row)
