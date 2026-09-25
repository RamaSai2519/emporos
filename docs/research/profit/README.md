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

**Finding (2026-09-25): the broker's daily history is already adjusted for splits and bonuses.** At 87
of the 89 splits and bonuses NSE lists for the 148 D1 names, the derived daily series is CONTINUOUS
across the ex-date. The local archive was fetched in chunks at different times: a chunk fetched before
an action holds raw prices, one fetched after it holds adjusted prices, so the break sits at a chunk
boundary (CANBK: the 1:5 split was 2024-05-15, the series steps by 0.1998 on 2022-05-10). Applying an
exchange ex-date to this series would double-adjust it and put 87 false jumps in it. So the exchange's
records are used as EVIDENCE for a gap and the factor is dated at the gap (`gap_matching`).

* Source: NSE's public corporate-actions feed, collected on 2026-09-25 on the operator's approval
  (PROFIT_PLAN §8): one request per name, 3 s apart, honest user agent, 148 names, 2,184 records, none
  refused. `corporate-actions.jsonl` (each record with its source URL and fetch date) and
  `corporate-actions-collected.jsonl` here.
* `config/universe/d1/adjustments.yaml`: 19 factors, each dated at the day the broker's history changes
  basis, with the exchange's exact ratio and the action it matches (`emporos research build-adjustments`).
* `emporos research audit-discontinuities` (result: `discontinuities.yaml`): 60 open gaps >= 15% in 148
  names: 19 EXPLAINED by an exchange action, 10 SPLIT_SHAPED with no action to match (flip-flop days in a
  few names), 31 UNEXPLAINED (10 of them 2020-03-23, the circuit-breaker crash; 3 on 2024-11-21, Adani
  lower circuits: real moves). All 41 are quarantined: the analysis series flattens each gap, which also
  removes a genuine move, never adds one.
* Dividends, rights issues and demergers are NOT adjusted (26 rights/demerger/other actions are listed
  in `corporate-actions-unadjusted.yaml`).
* Known limits: a split or bonus after the newest archive chunk shows up as a raw gap at its ex-date and
  is matched the same way; a genuine >= 15% earnings gap that no action explains is flattened too, which
  costs event strategies (A2) their largest reactions.

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
