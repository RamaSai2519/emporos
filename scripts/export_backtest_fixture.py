"""Exports the bars a backtest ran on, plus the instrument definitions, into committed fixtures.

The golden-file regression must not depend on a database that keeps changing, so the exact bars the
acceptance run used are frozen next to it, with a manifest whose SHA-256 pins the file: an edit to
the data is detected before it can quietly change a metric.

READ-ONLY against Mongo (`MONGO_URL`). Writes only under `tests/fixtures/backtest/`.

    pipenv run python scripts/export_backtest_fixture.py --from 2025-09-22 --to 2026-09-18 \
        --symbol RELIANCE-EQ --symbol TCS-EQ
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import InstrumentRecord
from emporos.persistence.repositories import InstrumentRepository

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "backtest"
BARS_FILE = "momentum_v1_5m_bars.csv.gz"
INSTRUMENTS_FILE = "instruments.json"
MANIFEST_FILE = "manifest.json"
COLUMNS = ["instrument_id", "ts", "open", "high", "low", "close", "volume", "partial"]


@dataclass(frozen=True)
class ExportRequest:
    symbols: tuple[str, ...]
    first: date
    last: date
    timeframe: Timeframe = Timeframe.M5


class BarCsv:
    """Bars <-> gzip CSV. Deterministic bytes: fixed mtime, sorted rows, exact decimal text."""

    def encode(self, bars: list[Candle]) -> bytes:
        text = io.StringIO()
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(COLUMNS)
        for bar in sorted(bars, key=lambda b: (b.instrument_id, b.ts)):
            writer.writerow(
                [
                    bar.instrument_id, bar.ts.astimezone(UTC).isoformat(),
                    format(bar.open.amount, "f"), format(bar.high.amount, "f"),
                    format(bar.low.amount, "f"), format(bar.close.amount, "f"),
                    bar.volume, int(bar.partial),
                ]
            )  # fmt: skip
        return gzip.compress(text.getvalue().encode(), mtime=0)


class FixtureExporter:
    def __init__(self, database: object, directory: Path = FIXTURE_DIR) -> None:
        self._store = MongoCandleStore(database)  # type: ignore[arg-type]
        self._instruments = InstrumentRepository(database)  # type: ignore[arg-type]
        self._directory = directory

    async def export(self, request: ExportRequest) -> dict[str, object]:
        records = [await self._record(symbol) for symbol in request.symbols]
        start = datetime.combine(request.first, time(0), tzinfo=IST).astimezone(UTC)
        end = datetime.combine(request.last + timedelta(days=1), time(0), tzinfo=IST)
        bars: list[Candle] = []
        for record in records:
            bars += await self._store.read(record.id, request.timeframe, start, end.astimezone(UTC))
        data = BarCsv().encode(bars)
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / BARS_FILE).write_bytes(data)
        (self._directory / INSTRUMENTS_FILE).write_text(
            json.dumps([self._instrument(r) for r in records], indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "file": BARS_FILE,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bars": len(bars),
            "timeframe": request.timeframe.value,
            "first_day": request.first.isoformat(),
            "last_day": request.last.isoformat(),
            "symbols": list(request.symbols),
            "source": "Angel One getCandleData 1m, derived to 5m by emporos.marketdata.timeframes",
            "note": "the broker's history has no bars for 15:15-15:20 IST on recent sessions",
        }
        (self._directory / MANIFEST_FILE).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    async def _record(self, symbol: str) -> InstrumentRecord:
        record = await self._instruments.get_by_symbol("NSE", symbol)
        if record is None:
            raise SystemExit(f"unknown symbol NSE:{symbol}")
        return record

    @staticmethod
    def _instrument(record: InstrumentRecord) -> dict[str, object]:
        return {
            "exchange": record.exchange,
            "token": record.token,
            "tradingsymbol": record.tradingsymbol,
            "name": record.name,
            "lot_size": record.lot_size,
            "tick_size": format(record.tick_size.amount, "f"),
            "valid_from": record.valid_from.astimezone(UTC).isoformat(),
        }


async def _main(request: ExportRequest) -> None:
    factory = MongoClientFactory(Settings.default())
    try:
        manifest = await FixtureExporter(factory.database()).export(request)
    finally:
        await factory.close()
    print(json.dumps(manifest, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    args = parser.parse_args()
    asyncio.run(_main(ExportRequest(tuple(args.symbol), args.first, args.last)))


if __name__ == "__main__":
    main()
