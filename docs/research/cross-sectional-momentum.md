# Cross-sectional residual momentum (EM-179)

`emporos.research.factors`/`cross_sectional`/`cross_sectional_study` extend the feature-research
engine (EM-178) to a cross-sectional question: does a stock's return relative to the market and
its sector — not its raw return — predict what it does next, once ranked against the rest of the
universe? Same layering as `research`'s other modules (above `backtest`/`strategies`/
`persistence`, never imported back), same evidence discipline (pre-declared hypothesis, immutable
holdout, every trial recorded).

## What it does, mapped to the acceptance criteria

* **Historical as-of universe** — a study is handed bars for whichever instruments
  `emporos.backtest.universe.AsOfInstruments.as_of(moment)` resolved for the run's first day, the
  same survivorship-safe resolver EM-177 uses; nothing here re-derives universe membership.
* **Beta/neutralize NIFTY and sector effects using only prior data** — `research.factors.
  MarketFactor` is the study universe's own equal-weighted bar-return series (this codebase
  ingests no separate NIFTY index feed — see the module docstring for why that is an honest,
  standard proxy, not a stand-in for real constituents/weights); `SectorFactor` does the same per
  sector via the same `SectorLookup` `research.regimes.SectorAxis` already uses.
  `research.factors.BetaEstimator` estimates joint market+sector loadings from a strictly prior
  trailing window of bar returns (a two-factor OLS, exact `Decimal` arithmetic, solved from the
  2x2 normal equations) — this bar's own realized return is available by the time it closes, so
  including it is not looking ahead.
* **15m/30m/60m residual returns** — `research.factors.ResidualSignalCalculator` combines a
  trailing k-bar compounded return with the estimated loadings to produce one number per
  instrument per bar: its return with the market's and sector's removed.
* **Rank the universe cross-sectionally; top/bottom tails; multiple holding horizons** —
  `research.cross_sectional.CrossSectionalEngine` ranks every instrument's residual signal at
  each bar, takes the configured top/bottom fraction, and evaluates EACH tail's forward
  performance over every holding horizon in a `ForwardReturnCalculator` — signal horizon and
  holding horizon are independent parameters, exactly as the acceptance criteria separate them.
* **Relative-volume, volatility and spread conditioning** — the SAME `VolatilityBucket`/
  `LiquidityBucket` axes EM-178 built, plus a new `research.regimes.SpreadProxyBucket`: this
  codebase has no Level-1 quote data, so effective spread is proxied by the Amihud (2002)
  illiquidity ratio (`|return| / volume`), a standard proxy in the academic literature for exactly
  this gap — documented as a proxy, not hidden as if it were a real quoted spread.
* **Gross and net performance at ₹50,000 capital** — `research.cross_sectional.TailReport`
  carries both; position size at each bar is capital split evenly across that bar's tail
  membership, and `research.costs.TransactionCostModel` (the same statutory-charges-plus-slippage
  model EM-178 built) is applied per position.
* **Pre-declared parameter sets and untouched holdout** — reuses EM-178's `domain.hypotheses.
  HypothesisDeclaration` and `research.hypotheses.HoldoutGate` unchanged: a TEST-role
  `CrossSectionalStudy.run` must cover exactly the declared holdout, a TRAIN run may not touch it.
* **Every trial recorded** — `domain.cross_sectional_trials.CrossSectionalTrial` (a sibling of
  `FeatureTrial`, not a repurposing of it — a tail's evidence has no per-instrument analogue) via
  `CrossSectionalTrialLedger`, with a Mongo-backed `persistence.cross_sectional_ledger.
  MongoCrossSectionalTrialLedger`.

## Real-data proof, 2026-09-23

Run entirely against the local Parquet cache (`FileCandleReader` — **zero Mongo, zero network**):
20 NSE instruments already warm in `~/.cache/emporos/candles` (a much wider slice than EM-178's
proof used, still without a single new Atlas read), 5m bars, TRAIN 2026-04-01..2026-06-30,
pre-declared holdout 2026-07 (the whole month), signal horizons 15m/30m/60m, holding horizons
15m/30m/60m, tail fraction 20%, 60-bar beta window, ₹50,000 capital, 5bp slippage. 252 trials
recorded (126 TRAIN + 126 TEST).

**Honest finding: reject, on both grounds the acceptance criteria name.**

1. **Costs.** Even where TRAIN showed a small, statistically significant raw edge — the top
   tail's 60-minute holding return averaged +0.023%/trade with a t-statistic of 3.9 — the
   round-trip cost assumption (statutory charges + 5bp slippage) turns every single pooled
   cell negative, TRAIN and TEST alike, top and bottom, every horizon: net expectancy sits
   around -0.17% to -0.22% per trade throughout. The in-sample edge is real but two orders of
   magnitude too small to survive trading it.
2. **Out-of-sample stability.** Even ignoring costs entirely, the pattern does not hold up.
   In TRAIN, both tails showed positive continuation (top tail's forward return mean was
   consistently positive across all three holding horizons). In the holdout, the top tail's
   15m/30m forward returns turned NEGATIVE (t = -2.25, -2.14) while the bottom tail's turned
   strongly POSITIVE (t up to 5.31) — the sign of the relationship did not survive the holdout,
   let alone its magnitude. That is exactly the overfitting signature a pre-declared,
   never-tuned-against holdout exists to catch.

Both findings point the same way: this feature, at this parameterization, is rejected — the same
verdict every strategy curated on this platform has reached so far (`docs/live-trading.md`), for
the same reason this engine exists: surface it before capital is risked, not after.

## What this deliberately does not do

* No CLI command — same reasoning as EM-178: the acceptance criteria did not ask for one.
* `AlignedUniverse` requires bars already position-aligned across instruments (same date range,
  same timeframe, one shared trading calendar); an instrument with a genuine feed gap is trimmed
  to the common timestamp set by the composition root (the proof script here), not silently
  reindexed by the library code itself.
* The market and sector factors are equal-weighted proxies built from the study's own universe,
  not free-float-market-cap-weighted NIFTY/sector indices — this codebase does not ingest index
  constituents or weights. A real index feed, if ever ingested, would replace `MarketFactor`
  without changing anything downstream of it (`ResidualSignalCalculator` only needs a return
  series).
* Beta estimation uses a joint two-factor regression, not the sequential (Gram-Schmidt-style)
  residualization academic "residual momentum" papers sometimes use; documented in
  `research/factors.py`'s docstring as the simplification it is.
