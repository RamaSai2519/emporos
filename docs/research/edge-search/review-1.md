# Edge search meta-review 1 (EDGE_SEARCH_PLAN §7.5), 2026-09-24

Written after iteration 12 (the §7.5 review was due at iteration 10). Every number below comes from
Discovery data (<= 2024-12-31) or from the trial ledgers. None comes from a Confirmation, holdout or
vault result. The volatility table uses **unsigned** moves only: no direction and no P&L. It can say
which days are able to pay for a trade, but it cannot suggest which way to trade.

## 1. Lessons so far (4 cells, 38 screen arms, all rejected)

| Cell | Outcome | What it taught |
|---|---|---|
| L3-raw-gap-hold-to-close | 6 INFEASIBLE, 10 SCREEN_REJECT | Continuation has no signal. The fade makes +0.07..0.10% gross, about a third of the cost. |
| L3-orb-high-rvol-wide-range | 12 INFEASIBLE | Gross rises with the opening-volume multiple but tops out at 0.20%. |
| L3-first-hour-shock-reversal | 6 INFEASIBLE | Waiting for a retrace lifts the fade to 0.17-0.35% gross, and it rises with gap size. |
| L3-first-hour-reversal-large-gap | 4 SCREEN_REJECT, **feasible** | 0.43% gross clears the 0.324% benchmark cost but not the 0.636% adverse break-even. The trade count sits at the 300 floor. |

Every arm that failed on cost failed the same way. Gross per trade grows only when the conditioning
set is restricted to rarer, bigger days, and that shrinks the trade count toward the S2 floor. The
constraint that binds is not the signal but **the size of the move available on 29 mega-caps**.

## 2. Structural finding A: the feasibility wall is a property of the universe

Median |open to 15:10 close| per stock-day, Discovery, the 29 names with ten years of bars
(58,850 stock-days). The §3.5 feasibility bar is 1.27% at Rs 25,000 and 0.99% at Rs 50,000.

| Slice | Median full-session move | Share of days >= 1.27% | Share of days >= 0.99% |
|---|---|---|---|
| All 29 names | **0.89%** | 0.33 | 0.44 |
| Most volatile name (NSE:25) | 1.31% | 0.51 | 0.60 |
| Least volatile name (NSE:1333) | 0.64% | 0.22 | 0.32 |
| INDIA VIX open, bottom quintile | 0.71% | | |
| INDIA VIX open, top quintile | 1.18% | | |

Consequences:
- **At Rs 25,000, 28 of 29 names are INFEASIBLE even for a full-session hold, and even in the
  top VIX quintile.** Any cell that holds for less than the full session, or does not select for
  unusually large days, is INFEASIBLE by construction. Running such cells spends trials (N) and
  iterations and cannot produce a candidate.
- At Rs 50,000 (one position using all the capital), the bar drops to 0.99%, and the top VIX
  quintile and the volatile half of the names clear it. Size is an operator decision (§8), filed
  below.
- The durable fix is **higher-volatility names** (D1, which should also cover mid-caps, not just
  NIFTY 100) and **event days** (D5), where moves are large by nature.

## 3. Structural finding B: the S4 DSR hurdle now needs an annualised Sharpe above 5

`emporos backtest trials program` on 2026-09-24: N = 14,066, of which 610 carry a Sharpe. Their
daily-Sharpe spread is 0.0796. `LuckBenchmark.of(14066, 0.0796)` gives a luck benchmark of **daily SR
0.314 (annualised 4.99)**. For DSR >= 0.95 (normal returns):

| True annualised Sharpe | Walk-forward days needed |
|---|---|
| 1 to 4 | never (below the luck benchmark) |
| 6 | about 710 |
| 8 | about 85 |

No realistic intraday cash strategy has an annualised Sharpe of 6. As the gate is priced today,
**S4 cannot be passed**, so the plan can only end EXHAUSTED, whatever the lanes find. The cause is a
mismatch: the spread is measured on 610 strategy trials (EM-114 arms, whose Sharpes differ mostly
because their *true* means differ), but it is multiplied by the expected-maximum quantile for all
14,066 looks. 13,288 of those looks are feature, cross-sectional and lead-lag grid points, which are
highly correlated and are not daily-Sharpe draws at all. Bailey and Lopez de Prado's own answer is to
use the **effective number of independent trials** (cluster the trials by return correlation, N =
number of clusters, spread measured across cluster representatives). That changes what "validated"
means, so it is an operator decision (§8), filed below. The agent must not change it.

Until the operator rules, the loop keeps running S1 to S3 unchanged. S2 and S3 do not use DSR, so a
candidate can still be found and parked at S4.

## 4. Priority changes (justified by §1 and §2 only)

1. **D1 moves ahead of further mega-cap cells.** The last three L3 cells each named it as the only
   permitted route. Scope: NIFTY 100 plus liquid NIFTY Midcap 150 names (median traded value high
   enough for Rs 50,000 at 10% of bar volume). Mid-caps are expected to be where full-session moves
   above 1.27% are ordinary. The first D1 check is to rerun §2's unsigned-move table on them. **Keep Atlas load down:** fetch in throttled, resumable batches, then roll up to cold and
   mirror to the Parquet cache. Never re-sweep. Survivorship: today's constituents projected back to
   2016 include names that grew into the index, so every D1 cell must use the EM-177 as-of universe
   rules, or declare the bias.
2. **D5 (event calendar) next**, in the foreground while D1 downloads. It unlocks 8 cells (L2 and
   L14-results-season), and earnings reaction days are the largest predictable moves in cash equity.
3. **D7 quote recording starts now.** The plan says "every day it isn't running is lost". Twelve
   iterations in, it has not started.
4. **Mega-cap cells before D1 lands:** only cells whose declared conditioning set selects large days
   *by construction* are worth running: L4-expected-range-filter, L4-india-vix-regime (top quintile),
   L4-atr-percentile-regime (top decile), L4-gap-fade-expected-range. Before committing each
   declaration, check S1 feasibility of the conditioning set on Discovery (§3.5 permits this and it
   still counts as a trial). Park L9, L11 and L14 cells on the mega-caps until D1 is in place: at
   Rs 25,000 they are INFEASIBLE by §2 and would only add to N.
5. **Hold to the session end.** In every cell so far the move accrued late (the 12:30 time stop
   halved gross). New declarations should make hold-to-15:15 the default arm, with shorter holds only
   where a rationale needs them.
6. **Fewer arms per cell.** N is already large enough that extra arms barely move the luck benchmark
   (it grows with sqrt(log N)), but each arm costs about 45 s of screen time. Declare 2 to 6 arms
   and spend iterations on new mechanisms, not grids.
7. **Screener speed** becomes the bottleneck once D1 multiplies the universe by 5 or more (9 minutes
   for 12 arms on 29 names today). Vectorise the scan path (numpy or polars over the Parquet cache)
   before the first D1 cell, keeping the Decimal evaluator as the parity oracle.

## 5. What the lanes should expect

Honest prior: on 29 mega-caps at Rs 25,000, finding an edge that pays 0.64% per trade after adverse
costs is unlikely. The search's expected value lies in (a) event days, (b) a volatile universe, and
(c) size, in that order. If D1 and D5 are exhausted without a candidate, the EXHAUSTED report should
say plainly that the cost wall at this capital is the binding constraint, and list the leverage
decision and product scope (§5, out of scope) as the frontiers.

## 6. Operator decisions filed (§8)

- **EM-207** Size: allow Rs 50,000 in a single position (at most one open position) for new declarations.
- **EM-206** DSR: price the S4/S5 hurdle at the effective number of independent trials.
- D1 constituent lists, if NSE's published lists cannot be fetched within their terms.
