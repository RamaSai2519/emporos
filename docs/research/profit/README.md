# Track A (swing / delivery): data and machinery

Foundations for PROFIT_PLAN §4. No cell has been declared or run.

## Daily bars (A-F1, EM-220)

`emporos research build-daily-bars` builds session OHLCV for the 148 D1 research names from the
local 5m archive (cache, then cold tier), through the vault reader, for Discovery and Confirmation
(2016-10-03 to 2026-03-18). Output: `~/.cache/emporos/candles-derived/1d/`, a DERIVED root beside
the candle cache, never inside it (the cache's own 1d files are the broker's daily bars and reach
into the vault month). `--reference` builds the indices and India VIX the same way; `--shard N/M`
runs M builds at once.

Known limits: the close is the last 5m close, not the exchange's official close (against the
broker's daily bars on 29 names: median 0.09% off, p99 0.53%, max 3.0%); volume is the sum of 5m
volume and misses auction volume (median 0.4% off); `partial` is set when a 5m bar in the session
spans a feed gap (23% of daily bars, so it is reported, not filtered).

## Corporate actions (A-F2, EM-221)

* `config/universe/d1/adjustments.yaml`: the factor ledger (ex-date, price ratio, kind, source).
  Every factor names its source. Empty until a source is confirmed and collected.
* `emporos research audit-discontinuities`: every open >= 15% from the previous close is EXPLAINED
  (a factor on record fits), MISMATCHED, SPLIT_SHAPED or UNEXPLAINED; all but the first are
  quarantined. Result: `discontinuities.yaml` here.
* `emporos research collect-actions` / `build-adjustments`: the resumable, polite collector for
  NSE's public corporate-actions feed and the builder that turns splits and bonuses into factors.
  Dividends, rights issues and demergers are NOT adjusted (rights and demergers are listed in
  `corporate-actions-unadjusted.yaml`).

## Delivery fees (A-F4, EM-222)

`config/fees/angelone-delivery-2026-09-25.yaml`, `verified: false`. `FeeSchedule.product` keeps it
out of every intraday reader.

## Swing screener (A-F5, EM-223): `emporos.research.swing`

Next-open fills on raw prices, whole shares, delivery costs at BENCHMARK (fees, 10 bps a side) and
ADVERSE (fees x1.5, 25 bps) scenarios, analysis-basis valuation (adjusted, quarantined gaps
flattened), the §3.2 bar (`SwingBar`), the same-universe equal-weight buy-and-hold through the same
simulator, the §3.4 block bootstrap, each arm's daily P&L series and days in cash, and a ledger
(`screens.jsonl`) that `ProgramTrialCount` counts.

Limits, by design: cash earns nothing; membership is today's constituents (`AllMembers`) until A-F3
lands; no liquidity or capacity check (Rs 10-50k positions in names trading >= Rs 10 crore a day);
fractional shares after a bonus are sold whole-share-rounded for fees only.
