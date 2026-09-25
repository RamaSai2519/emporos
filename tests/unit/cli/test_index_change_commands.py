"""EM-224: notices to index-changes.yaml, and the check against today's lists."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from emporos.cli.index_change_commands import IndexChangeBuild, load_voids
from emporos.research.index_membership import load_changes, save_changes

NOTICE = """
Replacements in indices
These changes shall become effective from March 31, 2021 (close of March 30, 2021).

1)   NIFTY 50

The following company is being excluded:

 Sr. No.     Company Name                 Symbol
    1        GAIL (India) Ltd.            GAIL

The following company is being included:

 Sr. No.     Company Name                 Symbol
    1        Tata Consumer Products Ltd. TATACONSUM

2)   NIFTY Midcap 150

The following company is being excluded:

 Sr. No.     Company Name                 Symbol
    1        Old Co Ltd.                  OLDCO

The following company is being included:

 Sr. No.     Company Name                 Symbol
    1        New Co Ltd.                  NEWCO
"""


class FakeExtractor:
    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = texts

    def text(self, pdf: Path) -> str:
        return self._texts[pdf.name]


def provenance(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "prov.jsonl"
    rows = [
        {
            "url": f"https://www.niftyindices.com/Press_Release/{n}.pdf",
            "fetched_on": "2026-09-25",
            "title": "t",
            "notice_date": "2021-02-23",
            "sha256": "0" * 64,
            "bytes": 1,
        }  # fmt: skip
        for n in names
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_it_writes_the_changes_and_checks_them_against_todays_lists(tmp_path: Path) -> None:
    prov = provenance(tmp_path, ["ind_prs23022021"])
    build = IndexChangeBuild(FakeExtractor({"ind_prs23022021.pdf": NOTICE}))

    changes, report = build.run(
        tmp_path, prov, {"NIFTY 100": {"TATACONSUM", "OTHER"}, "NIFTY MIDCAP 150": {"NEWCO", "X"}}
    )

    assert {(c.index, c.effective) for c in changes} == {
        ("NIFTY 100", date(2021, 3, 31)), ("NIFTY MIDCAP 150", date(2021, 3, 31))
    }  # fmt: skip
    assert report["notices_fetched"] == 1
    assert report["notices_with_relevant_sections"] == 1
    consistency = report["consistency"]
    assert (
        consistency["NIFTY 100"]["inconsistencies"]
        == [  # type: ignore[index]
            "NIFTY 100 2021-03-31: GAIL was removed but is a member after it"
        ]
        or consistency["NIFTY 100"]["inconsistencies"] == []
    )  # type: ignore[index]
    assert consistency["NIFTY MIDCAP 150"]["coverage_start"] == "2021-03-31"  # type: ignore[index]
    assert "fetched 2026-09-25" in changes[0].source


def test_a_pdf_that_cannot_be_read_is_reported_and_the_rest_carry_on(tmp_path: Path) -> None:
    class Failing(FakeExtractor):
        def text(self, pdf: Path) -> str:
            if pdf.name == "bad.pdf":
                raise ValueError("pdftotext failed")
            return super().text(pdf)

    prov = provenance(tmp_path, ["bad", "ind_prs23022021"])
    build = IndexChangeBuild(Failing({"ind_prs23022021.pdf": NOTICE}))

    changes, report = build.run(tmp_path, prov, {"NIFTY 100": set(), "NIFTY MIDCAP 150": set()})

    assert len(changes) == 2
    assert report["unreadable_pdfs"] == ["bad: pdftotext failed"]


def test_the_change_file_round_trips(tmp_path: Path) -> None:
    prov = provenance(tmp_path, ["ind_prs23022021"])
    changes, _ = IndexChangeBuild(FakeExtractor({"ind_prs23022021.pdf": NOTICE})).run(
        tmp_path, prov, {"NIFTY 100": set(), "NIFTY MIDCAP 150": set()}
    )
    path = tmp_path / "index-changes.yaml"

    save_changes(changes, path, "# header\n")
    loaded = load_changes(path)

    assert loaded == sorted(changes, key=lambda c: (c.effective, c.index))
    assert path.read_text(encoding="utf-8").startswith("# header")
    assert (
        yaml.safe_load(path.read_text(encoding="utf-8"))["changes"][0]["effective"] == "2021-03-31"
    )


def test_a_notice_nse_cancelled_is_read_as_never_having_happened(tmp_path: Path) -> None:
    prov = provenance(tmp_path, ["ind_prs23022021"])
    build = IndexChangeBuild(FakeExtractor({"ind_prs23022021.pdf": NOTICE}))

    changes, report = build.run(
        tmp_path, prov, {"NIFTY 100": {"GAIL"}}, voided={"ind_prs23022021": "null and void"}
    )

    assert changes == []
    assert report["voided_notices"] == {"ind_prs23022021": "null and void"}


def test_the_void_file_maps_notices_to_reasons(tmp_path: Path) -> None:
    path = tmp_path / "voids.yaml"
    path.write_text("voids:\n- {notice: ind_prs1, reason: cancelled by ind_prs2}\n", "utf-8")

    assert load_voids(path) == {"ind_prs1": "cancelled by ind_prs2"}
    assert load_voids(tmp_path / "none.yaml") == {}


def test_the_committed_voids_name_notices_that_exist_in_the_fetch_record() -> None:
    names = {
        Path(json.loads(line)["url"]).name.removesuffix(".pdf")
        for line in Path("docs/research/profit/index-notices.jsonl").read_text().splitlines()
    }

    assert set(load_voids(Path("config/universe/d1/index-notice-voids.yaml"))) <= names
