"""Raw upstream-format instrument rows for pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.instruments.downloader import DownloadedMaster

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "instrument_master_sample.json"


def sample_rows() -> list[dict[str, Any]]:
    """The recorded sample: 26 NSE/BSE cash rows (2 unusable) plus 5 out-of-scope rows."""
    return json.loads(SAMPLE.read_text())


def cash_rows(
    count: int, *, exchange: str = "NSE", first_token: int = 1000
) -> list[dict[str, Any]]:
    return [
        {
            "token": str(first_token + i),
            "symbol": f"SYM{first_token + i}-EQ",
            "name": f"SYM{first_token + i}",
            "expiry": "",
            "strike": "-1.000000",
            "lotsize": "1",
            "instrumenttype": "",
            "exch_seg": exchange,
            "tick_size": "5.000000",
        }
        for i in range(count)
    ]


def as_master(rows: list[dict[str, Any]]) -> DownloadedMaster:
    return DownloadedMaster(rows=tuple(rows), upstream_row_count=len(rows))


def instrument(token: str = "1000", **overrides: object) -> Instrument:
    """A valid domain `Instrument` (NSE cash, ₹0.05 tick) for differ/store/cache tests."""
    fields: dict[str, object] = {
        "exchange": Exchange.NSE,
        "token": token,
        "tradingsymbol": f"SYM{token}-EQ",
        "name": f"SYM{token}",
        "lot_size": 1,
        "tick_size": Money.of("0.05"),
    }
    return Instrument(**{**fields, **overrides})  # type: ignore[arg-type]
