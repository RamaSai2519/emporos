"""Wiring for the Track L Dev run (EM-240): the concrete clients, stores, bars and costs the
`eventtrader` package is written against. The composition root for that package: nothing under
`emporos.eventtrader` imports any of this.

The client stack, outermost first: token tally, date guard, journal (record on every real call),
USD ceiling, the HTTP client. The ceiling sits inside the journal so a recorded answer is free, and
the date guard sits outside it so it also guards the recordings. Keys come from the environment
only and are never logged."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.cli.corporate_actions_commands import DEFAULT_TOKENS, research_symbols
from emporos.cli.intraday_bars import VaultedIntradayBars
from emporos.cli.swing_worlds import delivery_schedule
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.eventtrader.events import ContextBuilder
from emporos.eventtrader.llm.budget import BudgetedClient, UsdBudget
from emporos.eventtrader.llm.client import LlmClient
from emporos.eventtrader.llm.guards import (
    CutoffGuardedClient,
    ScopedTallies,
    TallyingClient,
    TokenTally,
)
from emporos.eventtrader.llm.http_clients import (
    GATEWAY_MODEL,
    GATEWAY_URL,
    OPENAI_MODEL,
    OpenAiCompatibleClient,
)
from emporos.eventtrader.llm.journal import JournaledClient, JsonlJournal
from emporos.eventtrader.llm.pricing import ModelPrice, PriceTable
from emporos.eventtrader.pipeline import DecisionPipeline, PipelineConfig
from emporos.eventtrader.replay.engine import Decider, PostureSource, ReplayEngine, TokenMeter
from emporos.eventtrader.replay.options import ChainPlacer, NoChains, OptionChains
from emporos.eventtrader.replay.program_costs import program_costs
from emporos.eventtrader.replay.vaulted_market import (
    AdjustedBarLoader,
    BarLoader,
    NoAdjustment,
    ThreadedBarLoader,
    VaultedMarket,
    session_calendar,
)
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.runner import EngineFactory
from emporos.eventtrader.stages.stages import JudgeStage, PanelistStage, PostureStage, TriageStage
from emporos.eventtrader.variants import VariantSpec
from emporos.jev.config import JevConfig
from emporos.jev.leakage import KnowledgeCutoffGuard
from emporos.options.fo_costs import FoFeeScheduleLibrary
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.adjustments import AdjustmentLedger, PriceAdjuster
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_stock_chains import StockChains, StockLotBook
from emporos.research.market_context.bars import BarSeriesCache
from emporos.research.market_context.builder import AsOfContextBuilder, SectorMap
from emporos.research.scans.base import ScanExecution

__all__ = ["DevData", "DevPaths", "LlmStack", "declared_prices"]

NIFTY_ID = "NSE:99926000"
VIX_ID = "NSE:99926017"
MODEL_CUTOFF = date(2023, 10, 1)  # both declared models
CONTEXT_CACHE = 200  # every D1 name and the indices: events walk across all names in time order
CONTEXT_WARMUP_DAYS = 45  # calendar days of bars before the window, for the 20-session numbers
POSITION_VALUE = Decimal(50_000)
USD_INR = Decimal("88.00")


def declared_prices() -> PriceTable:
    """The prices the declaration fixes (USD per million tokens) and its USD/INR rate."""
    return PriceTable(
        {
            GATEWAY_MODEL: ModelPrice(Decimal("0.15"), Decimal("0.60")),
            OPENAI_MODEL: ModelPrice(Decimal("2.50"), Decimal("10.00")),
        },
        USD_INR,
    )


class LlmStack:
    """The clients for one run, with their token tallies. Mini goes through the gateway; the
    pinned gpt-4o only when asked for, and only with a USD ceiling."""

    def __init__(
        self,
        settings: Settings,
        journal: Path,
        prices: PriceTable,
        *,
        record: bool,
        mini_ceiling_usd: Decimal,
    ) -> None:
        self._settings, self._prices, self._record = settings, prices, record
        self._journal = JsonlJournal(journal)
        self._mini_ceiling = mini_ceiling_usd
        self.tally, self.scopes = TokenTally(), ScopedTallies()
        self.mini_budget = UsdBudget(mini_ceiling_usd)
        self._journaled: JournaledClient | None = None
        self._mini: LlmClient | None = None

    def mini(self) -> LlmClient:
        """Jev: openai/gpt-4o-mini through the Vercel gateway. One client for the whole run, so
        its journal counts are the run's."""
        if self._mini is None:
            self._mini = self._build_mini()
        return self._mini

    def _build_mini(self) -> LlmClient:
        inner: LlmClient | None = None
        if self._record:
            key = self._settings.vercel_gateway_key
            if not key:
                raise ConfigurationError(
                    "VERCEL_GATEWAY_KEY is not set (read from the environment)"
                )
            http = OpenAiCompatibleClient(GATEWAY_URL, key)
            inner = BudgetedClient(http, self.mini_budget, self._prices)
        self._journaled = JournaledClient(inner, self._journal, record=self._record)
        guard = CutoffGuardedClient(
            self._journaled, KnowledgeCutoffGuard(), {GATEWAY_MODEL: _config(GATEWAY_MODEL)}
        )
        return TallyingClient(guard, self.tally, self.scopes)

    @property
    def hits(self) -> int:
        return self._journaled.hits if self._journaled else 0

    @property
    def fresh(self) -> int:
        return self._journaled.fresh if self._journaled else 0

    def pipeline(self, spec: VariantSpec) -> DecisionPipeline:
        client = self.mini()
        panel = (
            PanelistStage.bull(client, GATEWAY_MODEL),
            PanelistStage.bear(client, GATEWAY_MODEL),
            PanelistStage.tape(client, GATEWAY_MODEL),
        )
        return DecisionPipeline(
            PipelineConfig(spec.triage_threshold, panel_enabled=spec.panel),
            TriageStage(client, GATEWAY_MODEL),
            panel,
            JudgeStage(client, GATEWAY_MODEL),
        )

    def posture_stage(self) -> PostureStage:
        return PostureStage(self.mini(), GATEWAY_MODEL)

    def triage_stage(self) -> TriageStage:
        return TriageStage(self.mini(), GATEWAY_MODEL)


def _config(model: str) -> JevConfig:
    return JevConfig(model=model, model_knowledge_cutoff=MODEL_CUTOFF)


@dataclass(frozen=True)
class DevPaths:
    manifest: Path = DEFAULT_MANIFEST
    tokens: Path = DEFAULT_TOKENS


class DevData:
    """Bars, calendar, context, market, chains and costs for one window."""

    def __init__(
        self,
        settings: Settings,
        first: date,
        last: date,
        stock_fo: FoDayStore | None,
        paths: DevPaths | None = None,
    ) -> None:
        paths = paths or DevPaths()
        vaulted = ThreadedBarLoader(VaultedIntradayBars.from_settings(settings))
        adjusted: BarLoader = AdjustedBarLoader(vaulted, PriceAdjuster(AdjustmentLedger.load()))
        self.first, self.last = first, last
        self.bars: BarLoader = adjusted
        self.symbols = research_symbols(paths.manifest, paths.tokens)
        self.instrument_ids = frozenset(self.symbols.values())
        self.sessions = session_calendar(adjusted, NIFTY_ID, first, last)
        warm = date.fromordinal(first.toordinal() - CONTEXT_WARMUP_DAYS)
        cache = BarSeriesCache(adjusted, warm, last, capacity=CONTEXT_CACHE)
        self.context: ContextBuilder = AsOfContextBuilder(cache, NIFTY_ID, VIX_ID, SectorMap({}))
        self.market = VaultedMarket(adjusted, NoAdjustment(), self.sessions, warm, last)
        self.chains: OptionChains = NoChains()
        if stock_fo is not None:
            self.chains = StockChains(stock_fo, StockLotBook.from_store(stock_fo))
        self.previous_session = dict(zip(self.sessions[1:], self.sessions[:-1], strict=False))

    def engines(self) -> EngineFactory:
        risk = RiskEngine()
        costs = program_costs(
            EarliestBeforeFirst(FeeScheduleLibrary.from_directory()),
            BenchmarkLoader().load(),
            delivery_schedule(),
            FoFeeScheduleLibrary.from_directory().earliest,
        )
        placer = ChainPlacer(self.chains, self.market, risk, costs)
        execution = ScanExecution(position_value=POSITION_VALUE)

        def build(
            decider: Decider, posture: PostureSource | None, tokens: TokenMeter | None
        ) -> ReplayEngine:
            return ReplayEngine(
                decider, self.context, self.market, risk, costs, execution,
                posture=posture, tokens=tokens, options=placer,
            )  # fmt: skip

        return build
