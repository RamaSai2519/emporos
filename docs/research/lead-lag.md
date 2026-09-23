# Intraday market/sector lead-lag research (EM-180)

`emporos.research.sessions`/`lead_lag`/`lead_lag_study` ask a session-structure question: does
the market's (or a sector's) move in the first N minutes of the day say anything about what
happens next — a stock's, or a sector's, subsequent or late-session return? Same layering,
provenance and evidence discipline as EM-178/179; this module leans hard on both, adding only what
is genuinely new: session-day splitting and within-day return extraction.

## What it does, mapped to the acceptance criteria

* **First 15m/30m/60m NIFTY and sector returns as predictors** — `research.sessions.
  early_return` compounds a within-day return series (`session_local_returns`) over the first N
  bars of a session. "NIFTY" is, as in EM-179, the study universe's own equal-weighted return
  series (no separate index feed is ingested); a sector's early return is the same function
  applied to an equal-weighted sector-member average instead — one function serves both, since
  neither cares what the series represents.
* **Subsequent 15m/30m/60m and late-session returns as targets** — `subsequent_return` (a fixed
  forward span) and `late_session_return` (everything left in the day) are the two target shapes
  the acceptance criteria name, both starting exactly where the early window ended.
* **Volatility/volume/market-regime conditioning** — the same `VolatilityBucket`/`LiquidityBucket`/
  `MarketRegimeAxis` axes from EM-178, fed a representative instrument's own bars causally, one
  label captured per day at the moment the early window closes.
* **Continuation and reversal, both tested** — not a separate mode: `LeadLagEngine` sorts every
  day into an "up" or "down" group by its predictor reading's sign, and
  `research.statistics.ExpectancyStats` (unchanged, from EM-178) reports THAT group's conditional
  expectancy — positive means continuation, negative means reversal, for whichever sign group it
  is. Both groups are always evaluated; nothing decides in advance which hypothesis is "the" one.
* **Stock-level and sector-level expressions** — `LeadLagEngine.evaluate` takes a predictor series
  and a target series with no opinion about what either represents; the real-data proof below
  runs the identical engine once per stock and once per (illustrative) sector grouping.
* **No overlapping-window leakage** — `subsequent_return`'s window begins at exactly
  `early_return`'s last bar, sharing an anchor PRICE with it but never a bar's own return; each
  day contributes exactly one predictor reading and one target reading per (early, target)
  horizon pair — no sliding per-bar sampling that would let adjacent, highly-correlated windows
  masquerade as independent observations.
* **Realistic costs/slippage and ₹50,000 portfolio constraints** — reuses `research.costs.
  TransactionCostModel` unchanged; a stock-level trial's position is sized by splitting the
  configured capital against the instrument's price. A sector-level target has no single
  instrument to price a round trip against, so its net figure is reported EQUAL to gross by
  design (`LeadLagEngine._net_return`'s documented fallback) — treat sector-level results as
  directional/diagnostic only, never as a tradable net figure; the stock-level results are where
  "net" is meaningful, exactly the same scope decision EM-179 made for its own sector-level
  numbers.
* **OOS and untouched holdout evidence** — reuses `HypothesisDeclaration`/`HoldoutGate` unchanged.

## Real-data proof, 2026-09-23

Run entirely against the local Parquet cache (`FileCandleReader` — **zero Mongo, zero network**):
20 NSE instruments already warm locally, TRAIN 2026-04-01..2026-06-30, pre-declared holdout
2026-07, early horizons 15m/30m/60m, target horizons 15m/30m/60m + late-session, 5 individual
stock targets, and two illustrative "sector" groupings (arbitrary 10/10 splits of the universe —
**this codebase has no real sector taxonomy ingested**, the same gap `opportunity.allocator.
SectorClassifier`'s docstring already documents; these groupings exist only to exercise the
sector-level code path, not as a real GICS/NSE classification). 2,016 trials recorded (1,036
TRAIN + 980 TEST).

**Honest finding: reject, for the same two reasons as EM-178/179, plus a third specific to this
run's breadth.**

1. **Costs.** Stock-level net expectancy is negative in nearly every cell, TRAIN and TEST alike —
   the round-trip cost assumption is larger than nearly every raw gross edge in the grid.
2. **The one cell that did clear costs in TRAIN didn't survive the holdout.** NSE:25, a 15-minute
   early "up" move predicting the late-session return, showed a genuinely interesting TRAIN
   result: gross +0.79%, t = 2.71, net (after costs) +0.59%. In the holdout, the same cell:
   gross -0.01%, net -0.21% — the edge evaporated completely out of sample.
3. **Multiple-testing honesty.** This proof declared ONE broad hypothesis covering the whole grid
   (5 subjects × 3 early horizons × 4 target horizons × 2 directions × conditioning axes), not a
   narrowed, specific pre-registered cell. With ~130 pooled cells per role, at least a few TEST
   cells look individually significant by chance alone (e.g. NSE:317, 30m-early "up" predicting
   late-session, TEST t = 2.33) — exactly the look-elsewhere effect `emporos.backtest.robustness`
   exists to correct for on the strategy side. None of those isolated TEST hits were flagged as
   promising in TRAIN first, so none of them should be read as validated evidence; a serious
   follow-up would pre-declare ONE specific cell (one subject, one early horizon, one target,
   one direction) before ever looking at its holdout, not mine this broad a grid and pick a
   winner after the fact.

Sector-level results (gross-only, no cost model — see above) show the same pattern: small,
inconsistent expectancies, no cell significant in both TRAIN and TEST at once.

## What this deliberately does not do

* Session boundaries are detected by grouping consecutive bars by IST calendar date
  (`split_sessions`), not by consulting a `TradingCalendar`/holiday list — a day with bars in the
  cache is a session by construction; a day with none simply produces no observation. Wiring in
  `emporos.history.calendar.StoredTradingCalendar` would only matter for deciding which days
  SHOULD have data, which is a data-completeness question, not this module's.
* No real sector taxonomy — see above; EM-181 or a future subtask may add one.
* Position sizing for a stock-level trial uses the session's own opening print as its
  representative price for the whole day, not a per-horizon price — a simplification, documented
  here rather than hidden.
