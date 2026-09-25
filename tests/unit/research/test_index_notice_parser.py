"""EM-224: reading NIFTY 50 / Next 50 / 100 / Midcap 150 changes out of a notice's text."""

from __future__ import annotations

from datetime import date

from emporos.research.index_notice_parser import (
    ChangeBuilder,
    NoticeParser,
    SectionKind,
)

REPLACEMENT_2020 = """
                                     Press Release
                                                                               July 02, 2020

                               Replacement in Indices
The Index Maintenance Sub-Committee (IMSC) of NSE Indices Limited has decided to make
replacements in various indices as given below:

A. Replacement on account of proposed voluntary delisting of Vedanta Ltd.:

The IMSC has decided to replace Vedanta Ltd. (VEDL) from various indices on account of
proposed voluntary delisting. The changes shall become effective from July 31, 2020 (close of
July 30, 2020).

1)      NIFTY 50

The following company is being excluded:

     Sr. No.                 Company Name                               Symbol
       1     Vedanta Ltd.                                        VEDL

The following company is being included:

     Sr. No.                Company Name                             Symbol
       1     HDFC Life Insurance Company Ltd.                    HDFCLIFE

2)      NIFTY Next 50

The following company is being excluded:

     Sr. No.                Company Name                             Symbol
       1     HDFC Life Insurance Company Ltd.                    HDFCLIFE

The following company is being included:

     Sr. No.                Company Name                              Symbol
       1     SBI Cards and Payment Services Ltd.                 SBICARD

3)      NIFTY 500

The following company is being excluded:

     Sr. No.                 Company Name                            Symbol
       1     Vedanta Ltd.                                     VEDL

4)      NIFTY 100

The following company is being excluded:

     Sr. No.                 Company Name                            Symbol
       1     Vedanta Ltd.                                     VEDL

The following company is being included:

     Sr. No.                Company Name                           Symbol
       1     SBI Cards and Payment Services Ltd.              SBICARD
"""

SEMI_ANNUAL_2017 = """
Press Release  Mumbai, February 14, 2017
Replacements in indices
These changes shall become effective from March 31, 2017 (close of March 30, 2017).

1)     NIFTY Midcap 150 Index

The following companies are being excluded:

 Sr. No.                        Company Name                            Symbol
    1      Inox Wind Ltd.                                           INOXWIND
    2      Just Dial Ltd.                                           JUSTDIAL

The following companies are being included:

 Sr. No.                        Company Name                              Symbol
    1      Apollo Hospitals Enterprise Ltd.                         APOLLOHOSP
    2      Bajaj Finance Ltd.                                       BAJFINANCE

2)      NIFTY Smallcap 250 Index
The following company is being excluded:
 Sr. No.   Company Name    Symbol
    1      X Ltd.                 XLTD
"""

WRAPPED_DATE = """
Replacements in Indices
The Index Maintenance Sub-Committee has decided to make the following replacement of stocks. These
changes shall become effective
from March 31, 2021 (close of March 30, 2021).

1)      NIFTY 50

The following company is being excluded:

     Sr. No.                 Company Name                                Symbol
       1     GAIL (India) Ltd.                                    GAIL

The following company is being included:

     Sr. No.                Company Name                              Symbol
       1     Tata Consumer Products Ltd.                          TATACONSUM
"""


class TestParser:
    def test_it_reads_the_effective_date_and_the_rows_of_each_wanted_section(self) -> None:
        parsed = NoticeParser().parse("ind_prs02072020", REPLACEMENT_2020)

        by_kind = {s.kind: s for s in parsed.sections}
        assert set(by_kind) == {SectionKind.NIFTY_50, SectionKind.NEXT_50, SectionKind.NIFTY_100}
        assert by_kind[SectionKind.NIFTY_50].effective == date(2020, 7, 31)
        assert by_kind[SectionKind.NIFTY_50].excluded == ("VEDL",)
        assert by_kind[SectionKind.NIFTY_50].included == ("HDFCLIFE",)
        assert by_kind[SectionKind.NEXT_50].included == ("SBICARD",)
        assert parsed.unread == ()

    def test_indices_we_do_not_use_are_ignored(self) -> None:
        parsed = NoticeParser().parse("x", REPLACEMENT_2020)

        assert all(s.kind is not None for s in parsed.sections)
        assert len(parsed.sections) == 3  # NIFTY 500 is not one of ours

    def test_a_trailing_index_word_and_run_together_names_are_matched(self) -> None:
        parsed = NoticeParser().parse("x", SEMI_ANNUAL_2017)

        (section,) = parsed.sections  # Midcap 150 Index; the Smallcap 250 section is not ours
        assert section.kind is SectionKind.MIDCAP_150
        assert section.effective == date(2017, 3, 31)
        assert section.excluded == ("INOXWIND", "JUSTDIAL")
        assert section.included == ("APOLLOHOSP", "BAJFINANCE")

    def test_an_effective_date_that_wraps_onto_the_next_line_is_found(self) -> None:
        (section,) = NoticeParser().parse("x", WRAPPED_DATE).sections

        assert section.effective == date(2021, 3, 31)

    def test_a_section_with_no_date_before_it_is_kept_but_reported(self) -> None:
        text = "1)  NIFTY 50\nThe following company is being excluded:\n  1   A Ltd.        AAA\n"

        parsed = NoticeParser().parse("x", text)

        assert parsed.sections[0].effective is None
        assert any("no effective date" in u for u in parsed.unread)

    def test_a_wanted_section_with_no_rows_is_reported_not_guessed(self) -> None:
        text = "effective from March 31, 2021\n1)  NIFTY 50\nNo change.\n"

        parsed = NoticeParser().parse("x", text)

        assert parsed.sections == ()
        assert any("no rows" in u for u in parsed.unread)

    def test_a_heading_that_names_ours_among_others_is_reported(self) -> None:
        text = "effective from March 31, 2021\n1)  NIFTY Midcap 150 and NIFTY Smallcap 250\nfoo\n"

        parsed = NoticeParser().parse("x", text)

        assert any("names one of ours" in u for u in parsed.unread)

    def test_thematic_indices_with_similar_names_are_not_reported(self) -> None:
        text = (
            "effective from March 31, 2021\n1)  NIFTY100 Quality 30\n"
            "2)  NIFTY Midcap150 Quality 50\n"
        )

        assert NoticeParser().parse("x", text).unread == ()

    def test_a_notice_about_other_things_reads_as_nothing(self) -> None:
        parsed = NoticeParser().parse("x", "Changes in Nifty Fixed Income indices w.e.f. Jan 1")

        assert (parsed.sections, parsed.unread) == ((), ())


class TestBuilder:
    def build(self, *texts: str):  # type: ignore[no-untyped-def]
        parsed = [NoticeParser().parse(f"n{i}", t) for i, t in enumerate(texts)]
        return ChangeBuilder().build(
            parsed, {f"n{i}": f"https://x/n{i}.pdf fetched 2026-09-25" for i in range(len(texts))}
        )

    def test_a_promotion_from_next_50_to_the_50_nets_out_of_nifty_100(self) -> None:
        changes, problems = self.build(REPLACEMENT_2020)

        (nifty100,) = (c for c in changes if c.index == "NIFTY 100")
        assert nifty100.effective == date(2020, 7, 31)
        assert nifty100.added == {"SBICARD"}  # HDFCLIFE moved Next 50 -> 50: no change to the 100
        assert nifty100.removed == {"VEDL"}
        assert problems == []

    def test_midcap_150_changes_carry_their_source(self) -> None:
        changes, _ = self.build(SEMI_ANNUAL_2017)

        (mid,) = changes
        assert mid.index == "NIFTY MIDCAP 150"
        assert mid.added == {"APOLLOHOSP", "BAJFINANCE"}
        assert "fetched 2026-09-25" in mid.source

    def test_an_explicit_nifty_100_section_is_used_only_without_the_components(self) -> None:
        only_100 = (
            "effective from March 31, 2021\n1) NIFTY 100\n"
            "The following company is being excluded:\n  1   A Ltd.        AAA\n"
            "The following company is being included:\n  1   B Ltd.     BBB\n"
        )

        changes, _ = self.build(only_100)

        assert [(c.index, sorted(c.added), sorted(c.removed)) for c in changes] == [
            ("NIFTY 100", ["BBB"], ["AAA"])
        ]

    def test_sections_without_a_date_are_skipped_and_said_so(self) -> None:
        text = "1)  NIFTY 50\nThe following company is being excluded:\n  1   A Ltd.        AAA\n"

        changes, problems = self.build(text)

        assert changes == []
        assert any("without an effective date" in p for p in problems)

    def test_changes_come_out_oldest_first(self) -> None:
        changes, _ = self.build(WRAPPED_DATE, REPLACEMENT_2020, SEMI_ANNUAL_2017)

        assert [c.effective for c in changes] == sorted(c.effective for c in changes)
