# Order-flow and liquidity-conditioned alpha research (EM-181)

`emporos.research.order_flow` adds four `Feature`s to EM-178's existing `FeatureRegistry`/
`AlphaDiscoveryEngine`/`FeatureStudy` machinery — this subtask needed no new engine, ledger or
persistence layer at all, only new features and one new segmentation axis
(`emporos.research.regimes.MomentumTailAxis`), since "does this feature carry forward-looking
edge, gross and net of cost, segmented by regime" is exactly what EM-178 already built.

## What "where the available broker/data feed supports it" means here

This codebase ingests and stores OHLCV candles only. Angel One's live feed DOES carry a best
bid/ask (`emporos.broker.models.Quote.bid`/`.ask`, sourced from `DepthLevel` in
`emporos.broker.angelone.mapping`), and the protocol has a full market-depth concept
(`QuoteDepth`), but DEPTH-mode subscription is explicitly out of v1 scope
(`emporos.broker.angelone.ws_market`), and even the best-bid/ask QUOTE stream is read live and
never persisted as a time series. So: real order-book imbalance and a real quoted spread are
NOT available to this research pipeline, which only ever evaluates already-stored historical
data — every feature below is an OHLCV-derived proxy, and each one's docstring says plainly what
real, tick-level measurement it stands in for and exactly how that substitution can mislead.

## What it does, mapped to the acceptance criteria

* **Signed-volume/order-imbalance proxy, limitations documented** — `OrderFlowImbalance`: a
  close-location-value ("did this bar close near its high or its low") weighted by volume, the
  money-flow-volume component of the classic Accumulation/Distribution line. Documented
  limitation: it assumes the bar's closing position approximates where most of its volume traded,
  which breaks down for a bar dominated by one early print followed by drift, or thin wicks that
  set the high/low without carrying real volume.
* **Relative volume normalized by time-of-day** — `RelativeVolumeTimeOfDay`: today's volume at
  this bar-of-day slot versus the trailing-`window`-session average at the SAME slot
  (`emporos.research.sessions.split_sessions` finds session boundaries), removing the ordinary
  U-shaped intraday volume curve a raw trailing average would mistake for a signal.
* **Spread, liquidity and trade-intensity features, where available** — `SpreadProxyFeature`
  exposes the Amihud illiquidity ratio (now factored out as `regimes.amihud_illiquidity`, shared
  with `SpreadProxyBucket`) as a continuous value; `TradeIntensity` is a raw, non-time-of-day-
  normalized trailing-volume z-score-style reading, deliberately kept distinct from
  `RelativeVolumeTimeOfDay` so it reacts to an intra-session burst the time-of-day version would
  by design smooth away; liquidity itself reuses EM-178's existing `LiquidityBucket`.
* **Standalone predictive information, and interaction with residual momentum/market regime** —
  every feature is evaluated pooled AND segmented by `VolatilityBucket`, `LiquidityBucket`,
  `SpreadProxyBucket`, `MarketRegimeAxis` (all EM-178), and the new `MomentumTailAxis` — a
  SELF-RELATIVE trailing-return percentile rank, standing in for `emporos.research.
  cross_sectional`'s true cross-sectional residual momentum, which a single-instrument causal
  `RegimeAxis` cannot compute (it needs the whole universe at once); documented as the
  simplification it is in `MomentumTailAxis`'s own docstring.
* **Explicit cost impact in high-imbalance/high-volatility periods** — read directly off the same
  trial ledger's volatility/spread-bucket segments: net expectancy in the `high` bucket versus
  `low`, no separate machinery needed.
* **Survive costs and OOS; reject if not** — `TransactionCostModel` (unchanged) plus TRAIN vs
  TEST-over-the-pre-declared-holdout via `HypothesisDeclaration`/`HoldoutGate` (unchanged).

**Bug found and fixed while building this**: `FeatureStudy`'s `trial_id` did not include the
trial's `role`, so a TRAIN run and a TEST run of the SAME hypothesis/feature/instrument/segment —
the canonical use of this whole holdout-discipline machinery — collided in one ledger and the
second `append` raised `DuplicateFeatureTrialError`. This surfaced immediately when this proof
tried to run both roles through one ledger. Fixed in `emporos.research.study.FeatureStudy._to_trial`
(role now included in the id, mirroring `CrossSectionalStudy`'s trial id, which already included
it), with a regression test (`test_a_train_run_and_a_test_run_of_the_same_hypothesis_do_not_collide`)
added to `tests/unit/research/test_study.py`. No prior committed work exercised a real TRAIN+TEST
pair through one ledger (EM-178's own proof was STANDALONE-only), so nothing else was depending on
the old id shape.

## Real-data proof, 2026-09-23

Run entirely against the local Parquet cache (zero Mongo, zero network): 10 NSE instruments, TRAIN
2026-04-01..2026-06-30, pre-declared holdout 2026-07, standard 5m/15m/30m/60m horizons, five
regime axes. 2,720 trials recorded per feature (680 TRAIN + 680 TEST) x 4 features = 10,880 total.

**Honest finding: reject, decisively, for all four features.** Every pooled gross expectancy,
every horizon, TRAIN and TEST alike, is within roughly ±0.06% per trade — far too small to be
distinguishable from noise, let alone survive costs. Net expectancy is essentially constant around
-0.13% to -0.18% per trade across every feature, every horizon and every regime bucket: the fixed
statutory-charges-plus-slippage assumption dominates completely, and none of the four proxies
carries anything close to enough standalone signal to matter.

* **Cost impact by regime**: net expectancy IS consistently a little more negative in high-
  volatility/high-spread buckets than low (e.g. `order_flow_imbalance`'s high-spread net
  -0.1750% vs low-spread -0.1685%), confirming the expected direction, but the gap here is small
  because `TransactionCostModel`'s slippage assumption is a FIXED basis-point figure per trade,
  not itself conditioned on the prevailing volatility/spread regime. A sharper, more realistic
  cost-impact study would scale the slippage assumption up in high-volatility/high-spread buckets
  (a natural extension for EM-183's portfolio-level cost model) rather than holding it fixed.
* **Momentum-tail interaction**: `order_flow_imbalance` shows the clearest interaction of the
  four — gross expectancy in the `high` self-relative-momentum bucket (+0.082%) versus `low`
  (-0.031%) — consistent with order-flow imbalance mattering more when an instrument is already
  trending. Even so, net expectancy stays solidly negative (~-0.17%) in every bucket, so this
  interaction does not change the reject verdict; it would be the natural next thing to test
  seriously (as a pre-declared, narrowed hypothesis) if a cheaper execution assumption ever made
  the gross edge worth chasing.

## What this deliberately does not do

* No real order-book imbalance or quoted spread — see above; this is the acceptance criteria's
  own anticipated limit ("where the available broker/data feed supports it").
* No real cross-sectional residual momentum interaction — `MomentumTailAxis` is a documented,
  self-relative proxy; wiring EM-179's actual `CrossSectionalEngine` output in as a precomputed
  per-(instrument, bar) label lookup is a natural, separable follow-up, not built here.
* The cost model's slippage assumption is not itself regime-conditioned; see the cost-impact
  finding above.
