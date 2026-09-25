"""`emporos research build-index-changes`: NIFTY 100 / Midcap 150 changes from the notices (EM-224).

Reads the PDFs `collect-index-notices` fetched (through `pdftotext -layout`), parses the NIFTY 50,
NIFTY Next 50, NIFTY 100 and NIFTY Midcap 150 sections, writes
`config/universe/d1/index-changes.yaml` and then CHECKS the result: today's two constituent
lists are rebuilt backwards through the changes and every place where a change does not fit (a
name added that is not a member afterwards, a name removed that still is) is listed. A clean list
means the notices are complete for that index; each entry is a missing notice, a symbol that was
renamed, or a mistake. Nothing is guessed at."""

from __future__ import annotations

import csv
import json
import subprocess
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Protocol

import typer
import yaml

from emporos.cli.index_notice_commands import DEFAULT_CACHE, DEFAULT_PROVENANCE
from emporos.core.errors import EmporosError
from emporos.research.index_membership import (
    DEFAULT_CHANGES,
    IndexChange,
    MembershipTimeline,
    save_changes,
)
from emporos.research.index_notice_parser import ChangeBuilder, NoticeParser, ParsedNotice

D1_DIR = Path("config/universe/d1")
DEFAULT_REPORT = Path("docs/research/profit/index-changes-report.yaml")
CURRENT_LISTS: Mapping[str, str] = {
    "NIFTY 100": "nifty100.csv",
    "NIFTY MIDCAP 150": "niftymidcap150.csv",
}
HEADER = (
    "# NIFTY 100 and NIFTY Midcap 150 constituent changes (PROFIT_PLAN §4 A-F3, EM-224), read\n"
    "# from NSE Indices press releases (niftyindices.com/press-release) by\n"
    "# `research build-index-changes`. NIFTY 100 is the union of NIFTY 50 and NIFTY Next 50.\n"
    "# Every entry names its source notice and the date it was fetched. What does not fit\n"
    "# today's lists is in docs/research/profit/index-changes-report.yaml.\n"
)

_NOTICES = typer.Option(DEFAULT_CACHE, help="The fetched notice PDFs.")
_PROVENANCE = typer.Option(DEFAULT_PROVENANCE, help="The fetch record.")
_OUT = typer.Option(DEFAULT_CHANGES, help="The change file to write.")
_REPORT = typer.Option(DEFAULT_REPORT, help="The consistency report to write.")
_D1 = typer.Option(D1_DIR, help="The directory of today's constituent lists.")


class TextExtractor(Protocol):
    def text(self, pdf: Path) -> str: ...


class PdftotextExtractor:
    def text(self, pdf: Path) -> str:
        done = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip
        if done.returncode != 0:
            raise ValueError(f"pdftotext failed on {pdf.name}: {done.stderr.strip()[:200]}")
        return done.stdout


def current_symbols(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["Symbol"].strip() for row in csv.DictReader(handle) if row.get("Symbol")}


class IndexChangeBuild:
    def __init__(self, extractor: TextExtractor) -> None:
        self._extractor = extractor

    def run(
        self,
        cache: Path,
        provenance: Path,
        current: Mapping[str, set[str]],
        as_of: date | None = None,
    ) -> tuple[list[IndexChange], dict[str, object]]:
        """`as_of` is the day `current` was fetched: a change announced but not yet effective on it
        is not in the list yet, so it is left out (and counted)."""
        rows = [
            json.loads(line) for line in provenance.read_text(encoding="utf-8").splitlines() if line
        ]
        sources = {
            Path(r["url"]).name.removesuffix(".pdf"): f"{r['url']} fetched {r['fetched_on']}"
            for r in rows
        }
        notices: list[ParsedNotice] = []
        unreadable: list[str] = []
        for row in rows:
            name = Path(row["url"]).name.removesuffix(".pdf")
            pdf = cache / f"{name}.pdf"
            try:
                notices.append(NoticeParser().parse(name, self._extractor.text(pdf)))
            except (ValueError, OSError) as error:
                unreadable.append(f"{name}: {error}")
        built, problems = ChangeBuilder().build([n for n in notices if n.sections], sources)
        changes = [c for c in built if as_of is None or c.effective <= as_of]
        report: dict[str, object] = {
            "current_lists_as_of": as_of.isoformat() if as_of else None,
            "changes_announced_but_not_yet_effective": len(built) - len(changes),
            "notices_fetched": len(rows),
            "notices_with_relevant_sections": sum(1 for n in notices if n.sections),
            "changes": len(changes),
            "unreadable_pdfs": unreadable,
            "unread_sections": {n.name: list(n.unread) for n in notices if n.unread},
            "builder_problems": problems,
        }
        checks: dict[str, object] = {}
        for index, symbols in current.items():
            timeline = MembershipTimeline(index, symbols, changes)
            checks[index] = {
                "current_members": len(symbols),
                "changes": sum(1 for c in changes if c.index == index),
                "coverage_start": (
                    timeline.coverage_start.isoformat() if timeline.coverage_start else None
                ),
                "inconsistencies": timeline.inconsistencies,
            }
        report["consistency"] = checks
        return changes, report


def research_build_index_changes(
    notices: Path = _NOTICES,
    provenance: Path = _PROVENANCE,
    out: Path = _OUT,
    report: Path = _REPORT,
    d1: Path = _D1,
) -> None:
    """Read the fetched notices into the change file and check it against today's lists."""
    try:
        current = {index: current_symbols(d1 / name) for index, name in CURRENT_LISTS.items()}
        as_of = date.fromisoformat(
            str(yaml.safe_load((d1 / "SOURCE.yaml").read_text())["fetched_on"])
        )
        changes, document = IndexChangeBuild(PdftotextExtractor()).run(
            notices, provenance, current, as_of
        )
        save_changes(changes, out, HEADER)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(yaml.safe_dump(document, sort_keys=False, width=140), encoding="utf-8")
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-index-changes failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{document['notices_fetched']} notices, {document['notices_with_relevant_sections']} with "
        f"NIFTY 50/Next 50/100/Midcap 150 sections, {len(changes)} changes written to {out}"
    )
    for index, check in document["consistency"].items():  # type: ignore[attr-defined]
        first = check["coverage_start"]
        typer.echo(
            f"{index}: {check['changes']} changes from {first}, "
            f"{len(check['inconsistencies'])} do not fit today's list"
        )
