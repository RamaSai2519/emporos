"""`emporos research audit-discontinuities` — size the corporate-action problem (EM-221, A-F2).

Reads the derived daily bars of the D1 research names (built by `build-daily-bars`, through the
vault reader, Discovery and Confirmation only), runs the >=15% open-gap audit against the committed
adjustment ledger, and writes what it found. Every gap the ledger does not explain is quarantined.
Nothing here touches the network or a database."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
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
from emporos.research.partition import CONFIRMATION, DISCOVERY

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
        out.parent.mkdir(parents=True, exist_ok=True)
        document = report.to_document(factors.content_hash)
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
        + f"; {len(report.quarantined)} quarantined; written to {out}"
    )
