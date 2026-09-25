"""`emporos research audit-discontinuities` — size the corporate-action problem (EM-221, A-F2).

Reads the derived daily bars of the D1 research names (built by `build-daily-bars`, through the
vault reader, Discovery and Confirmation only), runs the >=15% open-gap audit against the committed
adjustment ledger, and writes what it found. Every gap the ledger does not explain is quarantined.
Nothing here touches the network or a database."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

import typer
import yaml

from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.daily_bars_commands import derived_candle_root
from emporos.cli.vault_files import VaultFiles
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cache import CandleCacheFiles, FileCandleReader
from emporos.research.adjustments import DEFAULT_LEDGER, AdjustmentLedger
from emporos.research.d1_universe import DEFAULT_MANIFEST, D1Manifest
from emporos.research.discontinuities import DiscontinuityAudit, DiscontinuityStatus
from emporos.research.gap_classes import GapClass, GapClassifier
from emporos.research.partition import CONFIRMATION, DISCOVERY
from emporos.research.swing.regime import IndexSeries

NIFTY_50 = "NSE:99926000"
RULE = (
    "A gap the ledger explains is explained. Otherwise it is REAL when the NIFTY 50 close-to-close "
    "move that session has the gap's sign and at least half its size; else an ARTIFACT "
    "(neutralised) when it is split-shaped or larger than 25%; else REAL. Real gaps are traded "
    "through, never zeroed or quarantined (research.gap_classes)."
)
DEFAULT_REPORT = Path("docs/research/profit/discontinuities.yaml")

_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_LEDGER = typer.Option(DEFAULT_LEDGER, help="The adjustment-factor ledger.")
_OUT = typer.Option(DEFAULT_REPORT, help="Where the findings are written.")
_ROOT = typer.Option(None, help="Derived-candle root (default: beside the candle cache).")


def _daily_bars(reader: VaultedCandleReader, instrument_id: str) -> list[Candle]:
    """The daily bars of Discovery and Confirmation, the days the build covered."""
    first = datetime.combine(DISCOVERY.first, time(0, 0), tzinfo=IST)
    last = datetime.combine(CONFIRMATION.last + timedelta(days=1), time(0, 0), tzinfo=IST)
    return asyncio.run(reader.get_range(instrument_id, Timeframe.D1, first, last))


def research_audit_discontinuities(
    manifest: Path = _MANIFEST,
    ledger: Path = _LEDGER,
    out: Path = _OUT,
    root: Path | None = _ROOT,
) -> None:
    """Audit every >=15% open gap in the D1 names' daily bars against the adjustment ledger."""
    try:
        universe = D1Manifest.load(manifest)
        factors = AdjustmentLedger.load(ledger)
        reader = VaultedCandleReader(
            FileCandleReader(
                [CandleCacheFiles(root or derived_candle_root(Settings.default()))], memoize=False
            ),
            VaultFiles().load(),
        )
        report = DiscontinuityAudit(factors).audit_all(
            (i, _daily_bars(reader, i)) for i in universe.included
        )
        index = IndexSeries(_daily_bars(reader, NIFTY_50))
        classifier = GapClassifier(index)
        verdicts = {(f.instrument_id, f.day): classifier.classify(f) for f in report.findings}
        out.parent.mkdir(parents=True, exist_ok=True)
        document = report.to_document(factors.content_hash)
        document["rule"] = RULE
        document["by_class"] = {
            c.value: sum(1 for v in verdicts.values() if v.gap_class is c) for c in GapClass
        }
        for row in document["findings"]:
            verdict = verdicts[(row["instrument_id"], date.fromisoformat(row["day"]))]
            row["class"] = verdict.gap_class.value
            row["reason"] = verdict.reason
            row["index_move"] = None if verdict.index_move is None else f"{verdict.index_move:.4f}"
        out.write_text(yaml.safe_dump(document, sort_keys=False, width=120), encoding="utf-8")
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"audit-discontinuities failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    counts = report.by_status()
    typer.echo(
        f"{report.sessions_checked} sessions over {len(universe.included)} names, "
        f"{len(report.findings)} gaps >=15%: "
        + ", ".join(f"{status.value} {counts[status]}" for status in DiscontinuityStatus)
        + f"; by class {document['by_class']}; written to {out}"
    )
