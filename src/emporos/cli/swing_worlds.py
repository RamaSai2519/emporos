"""The two worlds a Track A screen runs in, loaded once and shared by the commands (EM-228, EM-233).

`StockWorldLoader` is the D1 stock universe: the derived daily bars through the vault reader
(Discovery only), the adjustment ledger, the NIFTY 50 series for the real-or-artifact rule, and the
200-session trend regime. `EtfWorldLoader` is the three index ETFs from the cold tier, with no
adjustment ledger, judged from the first session on which every ETF has 253 sessions of history.
A screen command picks the world(s) it needs and never reads a file itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.cold_storage import DEFAULT_COLD_DIR
from emporos.cli.daily_bars_commands import derived_candle_root
from emporos.cli.swing_bars import VaultedDailyBars
from emporos.cli.vault_files import VaultFiles
from emporos.core.config import Settings
from emporos.domain.fees import FeeSchedule, TradeProduct
from emporos.persistence.candle_cache import CandleCacheFiles, ColdArchiveFiles, FileCandleReader
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.adjustments import AdjustmentLedger
from emporos.research.d1_universe import D1Universe
from emporos.research.partition import DISCOVERY
from emporos.research.swing.loading import SwingDatasetBuild, SwingDatasetBuilder
from emporos.research.swing.regime import IndexSeries, IndexTrendRegime

__all__ = [
    "CAPITAL", "NIFTY_50", "REGIME_WINDOW", "EtfWorld", "EtfWorldLoader", "StockWorld",
    "StockWorldLoader", "delivery_schedule", "nifty_bars",
]  # fmt: skip

CAPITAL = Decimal(100_000)  # the declared capital of every Track A cell (PROFIT_PLAN)
NIFTY_50 = "NSE:99926000"
REGIME_WINDOW = 200
HISTORY_SESSIONS = 253  # an ETF's first judged session is its 253rd: 252 of history behind it


def delivery_schedule() -> FeeSchedule:
    return FeeScheduleLibrary.from_directory(product=TradeProduct.DELIVERY).earliest


def _vaulted(files: CandleCacheFiles | ColdArchiveFiles) -> VaultedDailyBars:
    return VaultedDailyBars(
        VaultedCandleReader(FileCandleReader([files], memoize=False), VaultFiles().load())
    )


def nifty_bars(root: Path | None) -> VaultedDailyBars:
    """The derived daily bars (NIFTY 50 among them), Discovery only."""
    return _vaulted(CandleCacheFiles(root or derived_candle_root(Settings.default())))


@dataclass(frozen=True)
class StockWorld:
    universe: D1Universe
    factors: AdjustmentLedger
    built: SwingDatasetBuild
    index: IndexSeries
    regime: IndexTrendRegime

    @property
    def label(self) -> str:
        return f"{self.universe.universe_label}+adj-{self.factors.content_hash[:8]}"


class StockWorldLoader:
    def __init__(self, manifest: Path, adjustments: Path, root: Path | None) -> None:
        self._manifest = manifest
        self._adjustments = adjustments
        self._root = root

    def load(self) -> StockWorld:
        factors = AdjustmentLedger.load(self._adjustments)
        if len(factors) == 0:
            raise ValueError(
                f"{self._adjustments} has no factors: PROFIT_PLAN §2.6 forbids a Track A screen "
                "before the corporate-action adjustment ledger is filled (EM-221)"
            )
        universe = D1Universe.load(self._manifest)
        bars = nifty_bars(self._root)
        index = IndexSeries(bars.bars(NIFTY_50, DISCOVERY.first, DISCOVERY.last))
        built = SwingDatasetBuilder(bars, factors, index).build(
            list(universe.instrument_ids), DISCOVERY.first, DISCOVERY.last
        )
        if built.names_without_bars:
            raise ValueError(
                f"no daily bars for {len(built.names_without_bars)} names "
                f"(first: {built.names_without_bars[0]}): run build-daily-bars first"
            )
        return StockWorld(universe, factors, built, index, IndexTrendRegime(index, REGIME_WINDOW))


@dataclass(frozen=True)
class EtfWorld:
    ids: dict[str, str]  # symbol -> instrument id
    built: SwingDatasetBuild
    index: IndexSeries
    start_day: date  # every arm and benchmark is judged from here


class EtfWorldLoader:
    def __init__(
        self, etf_report: Path, root: Path | None, prior_sessions: int = HISTORY_SESSIONS - 1
    ) -> None:
        """`prior_sessions`: how many sessions every ETF must have behind a judged session."""
        self._report = etf_report
        self._root = root
        self._prior = prior_sessions

    def load(self) -> EtfWorld:
        audit = yaml.safe_load(self._report.read_text(encoding="utf-8"))
        ids = {e["symbol"]: e["instrument_id"] for e in audit["etfs"]}
        first = min(date.fromisoformat(e["first_day"]) for e in audit["etfs"])
        settings = Settings.default()
        cold = _vaulted(ColdArchiveFiles(Path(settings.cold_archive_dir or DEFAULT_COLD_DIR)))
        index = IndexSeries(nifty_bars(self._root).bars(NIFTY_50, DISCOVERY.first, DISCOVERY.last))
        built = SwingDatasetBuilder(cold, AdjustmentLedger(), index).build(
            list(ids.values()), first, DISCOVERY.last
        )
        if built.names_without_bars:
            raise ValueError(f"no bars for {built.names_without_bars}: run fetch-etf-bars first")
        start_day = max(
            built.dataset.series(i).days[self._prior] for i in built.dataset.instrument_ids
        )
        return EtfWorld(ids, built, index, start_day)
