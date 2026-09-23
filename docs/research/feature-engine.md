# The feature research and alpha discovery engine (EM-178)

`emporos.research` is a leakage-safe framework for asking one question — "does this computed
feature carry forward-looking edge?" — with a paper trail, before any of it becomes a strategy. It
sits above `backtest`/`strategies`/`persistence` in the layering (like `opportunity` and `jev`):
nothing below it imports it back (`pyproject.toml`'s import-linter contracts).

## What it does, mapped to the acceptance criteria

* **Versioned feature registry with provenance** — `research.features.FeatureDefinition` is
  content-hashed (`core.hashing`, the same mechanism EM-177's `ResearchProvenance` uses for
  datasets); `FeatureRegistry` refuses to re-register the same name+version, so a feature's
  behaviour can never quietly change under research already run against it.
* **Causal-only computation** — structurally enforced, not just documented: `Feature.compute`
  receives a `CausalHistory`, a view over the bar series that raises `IndexError` on any attempt
  to read past the bar being evaluated. A feature that only reads through the view it is given
  cannot see the future even by mistake.
* **Forward returns at 5m/15m/30m/60m** — `research.horizons.ForwardReturnCalculator` expresses
  each horizon as a whole number of the study's base-timeframe bars (never resampled); a horizon
  that does not divide evenly is refused at construction.
* **Expectancy statistics** — `research.statistics.ExpectancyStats` reports conditional
  expectancy, median return, hit rate, rank IC (Spearman, tie-averaged), decile spread, a
  t-statistic and sample size, all exact `Decimal` arithmetic via `DecimalMath`.
* **Regime segmentation** — `research.regimes`: `VolatilityBucket` and `LiquidityBucket` are
  causal trailing-percentile classifiers (the same technique `MarketRegimeClassifier` uses for
  volatility); `MarketRegimeAxis` composes the existing live regime classifier rather than
  duplicating it; `SectorAxis` takes an injected lookup (Interface Segregation — no dependency on
  `emporos.opportunity`'s classifier).
* **Transaction-cost-adjusted expectancy** — `research.costs.TransactionCostModel` reuses
  `IntradayCharges`/the dated fee schedule for statutory round-trip charges, plus a slippage
  assumption in basis points on both legs. This is deliberately minimal, not EM-183's portfolio
  cost model: no market-impact/tiered-liquidity component yet.
* **Every trial recorded in a ledger** — `research.ledger.FeatureTrialLedger` (in-memory) and
  `persistence.feature_ledger.MongoFeatureTrialLedger` are append-only, same pattern as the
  strategy `TrialLedger`, over their own collection (`feature_trial_ledger`) — a feature trial
  carries statistics a strategy trial has no room for, so it is a sibling record
  (`domain.feature_trials.FeatureTrial`), not a repurposing of `Trial`.
* **Pre-declared hypotheses and immutable holdout periods** — `domain.hypotheses.
  HypothesisDeclaration` is append-only (no update path); `research.hypotheses.HoldoutGate`
  raises `HoldoutViolation` if a TEST-role trial's dates are not EXACTLY the pre-declared holdout,
  or if a TRAIN/STANDALONE trial touches any day inside one — mirroring EM-177's
  `ResearchIntegrityGate` (a structural, raising invariant, not a warning).

## Real-data proof, 2026-09-23

Run entirely against the local Parquet cache (`FileCandleReader` — **zero Mongo, zero network**,
per the standing "don't exhaust Atlas" constraint): a 12-bar trailing-momentum feature
(`nbar_momentum@v1`) over 5 NSE instruments (NSE:25, NSE:2885, NSE:11536, NSE:317, NSE:3499), 5m
bars, 2026-06-01..2026-07-31 (TRAIN role; the pre-declared holdout is 2026-08), 4 horizons, 2
regime axes.

140 trials recorded from one run (20 pooled + 120 regime-segmented). Pooled result, honestly:
**no feature survives its transaction-cost assumption.** Every instrument's cost-adjusted
expectancy across every horizon is negative (roughly -0.09% to -0.26% per trade after a 5bp
slippage assumption and statutory charges), even where the raw, pre-cost expectancy or t-statistic
looked interesting in isolation (e.g. NSE:317 60m: raw expectancy +0.117%, t-stat 5.49, but
cost-adjusted -0.089%). This is the expected, honest outcome for a naive, undeclared-as-serious
feature — consistent with every strategy curated so far under this platform being `rejected` for
the same reason (`docs/live-trading.md`): real trading costs are the first thing that kills a
plausible-looking signal, which is exactly what this engine is built to surface before capital is
risked on it, not after.

This was a STANDALONE proof run, not a real hypothesis: no feature has yet been seriously
pre-declared and tested through its holdout. That is the next real use of this engine, for a
future subtask (EM-179 onward), not this one.

## What this deliberately does not do

* No CLI command yet — the acceptance criteria did not ask for one, and none of the pipeline
  needs it to be tested; a `emporos research` command group is a natural, separable follow-up.
* No cross-sectional liquidity/ADV rank — `LiquidityBucket` ranks an instrument against its own
  trailing history only, not the universe's; EM-181's order-flow work may add a real ADV series.
* No market-impact cost model — `TransactionCostModel` is statutory charges plus a flat slippage
  assumption; EM-183 owns the portfolio-level, tiered cost model.
