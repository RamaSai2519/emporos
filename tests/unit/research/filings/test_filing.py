"""EM-239: an NSE announcement as a point-in-time filing."""

from __future__ import annotations

import json
from datetime import date

import pytest

from emporos.core.clock import IST
from emporos.research.filings.filing import parse_nse_filings

URL = "https://www.nseindia.com/api/corporate-announcements?symbol=ABB"
DAY = date(2026, 9, 25)


def row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "an_dt": "31-Jan-2024 17:36:34", "exchdisstime": "31-Jan-2024 17:36:40",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/X.pdf",
        "attchmntText": "Pursuant  to Regulation 39(3)\n we enclose",
        "desc": "Loss of Share Certificates",
        "seq_id": "105741130", "sm_isin": "INE002A01018", "sm_name": "Reliance Industries Limited",
        "symbol": "RELIANCE",
    }  # fmt: skip
    return {**base, **over}


def parse(*rows: dict[str, object]):  # type: ignore[no-untyped-def]
    return parse_nse_filings(json.dumps(list(rows)).encode(), URL, DAY)


class TestParse:
    def test_the_public_time_is_the_exchange_dissemination_time_not_the_company_s(self) -> None:
        (filing,) = parse(row())

        assert filing.published_at.isoformat() == "2024-01-31T17:36:40+05:30"
        assert filing.submitted_at is not None
        assert filing.submitted_at.isoformat() == "2024-01-31T17:36:34+05:30"
        assert filing.published_at.tzinfo == IST

    def test_it_keeps_the_categories_ids_links_and_provenance(self) -> None:
        (filing,) = parse(row())

        assert (filing.source, filing.symbol, filing.isin) == ("NSE", "RELIANCE", "INE002A01018")
        assert filing.category == "Loss of Share Certificates"
        assert filing.source_id == "105741130"
        assert filing.attachment_url.endswith("X.pdf")
        assert (filing.source_url, filing.fetched_on) == (URL, DAY)

    def test_the_feed_text_is_whitespace_normalised(self) -> None:
        (filing,) = parse(row())

        assert filing.subject == "Pursuant to Regulation 39(3) we enclose"

    def test_a_missing_dissemination_time_falls_back_to_the_company_time(self) -> None:
        (filing,) = parse(row(exchdisstime=None))

        assert filing.published_at.isoformat() == "2024-01-31T17:36:34+05:30"

    def test_a_row_with_no_time_at_all_is_refused(self) -> None:
        with pytest.raises(ValueError, match="without a time"):
            parse(row(exchdisstime=None, an_dt=None))

    def test_a_reply_that_is_not_a_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a list"):
            parse_nse_filings(b"{}", URL, DAY)

    def test_a_null_attachment_and_text_become_empty_strings(self) -> None:
        (filing,) = parse(row(attchmntFile=None, attchmntText=None))

        assert (filing.attachment_url, filing.subject) == ("", "")

    def test_a_dash_in_place_of_an_attachment_link_is_no_attachment(self) -> None:
        (filing,) = parse(row(attchmntFile="-"))

        assert filing.attachment_url == ""
