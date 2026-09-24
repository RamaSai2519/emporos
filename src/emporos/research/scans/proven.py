"""The scans whose trades were held to the real engine (EM-191 F3b, plan §4.1 F3 parity).

A name is listed here only because `tests/unit/research/test_scan_parity.py` runs EVERY entry
through the real `BacktestEngine` and checks the trade set and the net expectancy against it. The
test iterates this mapping, so adding a scan without proving it fails the build. A scan not listed
is advisory: a new scan for a new hypothesis gets its own parity case before its screens count.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from emporos.research.scans.base import IntradayScan, ScanExecution
from emporos.research.scans.orb import orb_scan
from emporos.research.scans.rsi_pullback import rsi_pullback_scan
from emporos.research.scans.vwap_reversion import vwap_reversion_scan

__all__ = ["PARITY_PROVEN", "ScanBuilder", "is_parity_proven"]

# (the strategy's parameters, the execution assumptions) -> the scan
ScanBuilder = Callable[[Any, ScanExecution], IntradayScan]

PARITY_PROVEN: Mapping[str, ScanBuilder] = {
    "orb_v1": orb_scan,
    "vwap_reversion_v1": vwap_reversion_scan,
    "rsi_pullback_v1": rsi_pullback_scan,
}


def is_parity_proven(scan_name: str) -> bool:
    return scan_name in PARITY_PROVEN
