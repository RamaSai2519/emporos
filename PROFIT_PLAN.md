# PROFIT_PLAN — a durable monthly income from ₹1,00,000, reinvested

**Owner:** the head session (operator-appointed strategist). **Workers:** Agent 1 and Agent 2 (and
any loop the head starts). **Jira:** a new epic under project EM; every task below is a ticket.
**Supersedes the scope** of `EDGE_SEARCH_PLAN.md` §5 "Out of scope" and `plan.md` "equity cash +
intraday only": the operator authorised (2026-09-25) any product that works with ₹1,00,000 live —
delivery/swing, futures, options — and asked to keep intraday research running. `EDGE_SEARCH_PLAN.md`
stays the rulebook for the intraday lane (Track C) unchanged.

## 0. Goal, and what counts

- **Target:** net **1.5-3% per month** on deployed capital, with most months positive and the worst
  month capped. Capital grows by reinvestment plus the operator's monthly additions. Anything above
  3%/month in a backtest is treated as suspect until the vault and paper confirm it.
- **Not a goal:** trading every day. Every strategy MUST have an explicit "stand aside" state (cash)
  and must spend many days in it.
- **Aggressive is allowed, ruin is not.** Aggressive arms (more concentration, fewer names, MTF or
  MIS leverage, larger option size) are declared and tested like any other arm, and each must report
  its risk of ruin (§3.4). No live strategy may carry an unhedged tail: no naked short options, no
  un-stopped leveraged position held overnight.
- **Order of delivery:** Track A (swing) first, Track B (index options) second, Track C (intraday)
  continuously in the background.

## 1. Why this plan (evidence, 2026-09-25)

19 intraday iterations, 66 screen arms, N = 14,090: no intraday cash signal pays its costs. The one
positive-gross mechanism (the first-hour fade of an overnight shock) was 0.14-0.43% gross on 29
mega-caps and **about 0% on the 148-name D1 universe** (EM-213): the near-miss was small-sample. In
every cell the move accrued late and hold-to-close beat shorter holds: the horizon, not the signal,
is the binding constraint. Round-trip cost is a fixed ~0.23-0.5%; a 5-20 day move in a mid-cap is
3-8%. So the program moves to longer holds, and to a real risk premium (index option volatility).

## 2. Anti-self-deception rules (bind every track; the head may not relax them)

1. **Declare before looking.** `config/experiments/<slug>.yaml`, committed before any directional
   run: rationale (who is on the other side and why they lose), falsification, full grid. 2-8 arms.
2. **Count every trial.** Every screen of every track is one line in a ledger that
   `ProgramTrialCount` counts. Tracks A and B write `docs/research/profit/screens.jsonl`; Track C
   keeps `docs/research/edge-search/screens.jsonl`.
3. **Splits** (unchanged from EDGE_SEARCH_PLAN §4.3): Discovery ≤ 2024-12-31; Confirmation
   2025-01-01..2026-03-18; Vault 2026-03-19..2026-09-18 (sealed, `VaultGate`, 3 opens program-wide);
   the D1 seeded 30% instrument holdout is never read. Children come from Discovery output only.
4. **Unsigned design only.** Before a declaration, a worker may look at magnitudes (move sizes,
   turnover, capacity), never at direction or P&L of the rule being designed.
5. **Survivorship.** D1 is today's NIFTY 100 + Midcap 150. A long-only multi-day rule on today's
   winners is flattered by construction. Every Track A result is judged AGAINST the equal-weight
   buy-and-hold of the SAME universe over the SAME days, net of the same costs (the bias inflates
   both), and the as-of membership work (A-F3) must land before any Track A candidate reaches S4.
6. **Corporate actions.** A multi-day return across an unadjusted split or bonus is garbage. No
   Track A screen runs before the adjusted series (A-F2) is in place.
7. **Costs are never loosened.** Dated fee schedule per product (`config/fees/`), benchmark and
   adverse scenarios as in §3. Unreconciled schedules carry `verified: false`; the operator
   reconciles against a contract note.

## 3. Fixed bars (set by the head before any Track A/B result; changes need the operator)

### 3.1 Costs
| Product | Benchmark slippage per side | Adverse |
|---|---|---|
| Delivery (Track A) | 10 bps | 1.5x fees + 25 bps |
| Index options (Track B) | half the recorded bid-ask, else 1 tick + 0.5% of premium | 1.5x fees + 2 ticks + 1.5% of premium |

### 3.2 Track A / B screen (S2, Discovery), all must hold
- Net CAGR ≥ 18% at benchmark costs **and** > 0 at adverse costs.
- Beats the same-universe equal-weight buy-and-hold on net Sharpe (Track A) / beats cash at 6.5%
  (Track B).
- ≥ 60% of calendar months net positive; worst month ≥ -10%; max drawdown ≤ 25%.
- Monthly-return t-stat ≥ 2.5; ≥ 100 round trips; positive in ≥ 60% of calendar years.
- No instrument > 25% of net profit (Track A).
- Parameter neighbours: ≥ 50% of declared adjacent arms also net positive at benchmark.

### 3.3 Later stages (as EDGE_SEARCH_PLAN §6, adapted)
- **S3 Confirmation:** net > 0 at adverse, same sign, beats the benchmark of 3.2.
- **S4 Walk-forward:** ≥ 3 windows, ≥ 60% positive, DSR ≥ 0.95 with N = all Track A+B trials. The
  head's decision, recorded here: Tracks A/B test pre-registered, published anomalies on a different
  horizon and product, so the 14k intraday looks are not candidates for selection among them. The
  operator may overrule.
- **S5 Vault:** one open for one frozen candidate, as §3.6 of EDGE_SEARCH_PLAN.
- **S6 Paper:** Track A ≥ 30 closed trades and ≥ 2 months; Track B ≥ 3 monthly expiries. Realised
  within the backtest's 90% prediction interval.

### 3.4 Risk of ruin (every arm, reported, and a pass condition for aggressive arms)
Block-bootstrap the arm's daily P&L (20-day blocks, 10,000 paths, 12 months): report P(drawdown ≥
30%) and P(12-month net < 0). An aggressive arm passes only with P(drawdown ≥ 30%) ≤ 5%.

### 3.5 Live risk rules (to be built into the risk engine before any live order)
Max loss per swing position 2% of capital (stop); max loss per option spread (width × lot) 5% of
capital; month drawdown -6% stops new entries until next month; -15% from peak halts all and pages
the operator; no averaging down; no naked short options.

## 4. Track A — swing / delivery (Agent 1)

Foundations first (tickets, in order):
- **A-F1 Daily bars for D1.** Build 1d bars for the 148 research names from the 5m cold archive
  (session OHLC, volume) into the 1d cache; never read the holdout.
- **A-F2 Adjusted series.** Corporate actions (splits, bonuses; dividends noted as a known limit)
  from NSE's corporate-actions records within their terms (operator authorised NSE endpoints for
  D5), ex-date timestamped; adjusted prices for signals, raw prices for fills. Cross-check against the
  ≥ 15% discontinuity finder: every discontinuity must be explained by an action or quarantined.
- **A-F3 As-of index membership.** NIFTY 100 / Midcap 150 historical changes (NSE index
  reconstitution notices). A name is tradable on a day only if it was a member then.
- **A-F4 Delivery fee schedule** (`config/fees/angelone-delivery-<date>.yaml`, `verified: false`):
  STT both sides, stamp on buy, DP charge on sell, brokerage as published.
- **A-F5 Swing portfolio screener** in `emporos.research`: daily-bar, portfolio-level (holdings,
  rebalance calendar, cash when the regime filter is off, whole shares at the declared capital),
  costs per trade, the 3.2 metrics, the same-universe benchmark, the 3.4 bootstrap. Tests pin every
  rule; parity checked against a hand-computed small case. `ProgramTrialCount` must count the
  Track A/B ledger (`docs/research/profit/screens.jsonl`) before the first Track A screen.

Cells (declare each, 2-8 arms; priors from the literature, not from our data):
- **A1 Momentum with a trend filter:** rank by 6-12 month return skipping the last month (or 52-week-
  high proximity); hold top 10-15; rebalance weekly or monthly; cash when NIFTY 50 closes below its
  200-day average. Aggressive arm: top 5.
- **A2 Post-earnings drift (D5 events on hand):** after the reaction session, enter names whose
  reaction-day return (or gap) is in the top decile of that season; hold 20 / 40 / 60 sessions.
- **A3 Weekly reversal among liquid names**, only when the market is above trend (buying losers in a
  falling market is catching knives). Survivorship makes this especially flattering: judge against
  the benchmark strictly.
- **A4 Combination rule** (declared before components are known): equal-risk blend of every A-cell
  that passes S3.

## 5. Track B — index options (Agent 2, foundations now; cells after Track A's first result)

- **B-F1 Data.** NSE F&O daily bhavcopy archive (every contract's OHLC, settle, OI; free, public),
  fetched once, politely, within NSE's terms, to Parquet. Record the lot size and weekly-expiry
  calendar in force on each date (both changed in 2024-2025). Say plainly what is missing: no
  intraday option prices, no historical bid-ask.
- **B-F2 Options backtester** at EOD granularity: defined-risk spreads only (put credit spread, iron
  condor), entry and exit at the close, SPAN-like margin estimate for the spread, costs from a dated
  F&O fee schedule (`verified: false`).
- **Cells:** B1 put credit spread ~1 sd OTM, 20-45 DTE, only when NIFTY is above trend and India VIX
  is in its middle band, skipping budget/RBI/election weeks; exits at 50% of credit or 2x loss or
  7 DTE. B2 iron condor, same filters. Aggressive arm: 2 lots. Naked short straddles may be
  BACKTESTED once, as a documented comparison of tail risk, never as a live candidate.

## 6. Track C — intraday, continuous (Agent 2 as a loop)

`EDGE_SEARCH_PLAN.md` unchanged, with review-2 and this plan's §1 as the steering notes. Priorities:
L17's children if it earns any gross; mechanisms not yet tried on D1 (L5 trend days with the index,
L13 passive entries, L14 expiry and calendar effects, L8 low-turnover residual reversal); aggressive
arms at MIS leverage (₹1L-2.5L positions) are allowed as research arms per EDGE_SEARCH_PLAN L1, and
report §3.4. Iteration budget: one cell per wake, 2-6 arms.

## 7. How the workers operate

- Each worker uses its **own git worktree and branch**, rebases on master before committing, and
  fast-forwards master. A ledger (`*.jsonl`) conflict is resolved by keeping both sides' lines.
- AGENTS.md applies in full: Jira first, OOP/SOLID, the five DoD gates before every commit, no
  mongomock, keep load off Atlas (read the local Parquet caches; fetch once, throttled).
- **No live orders, ever**, from any worker. Paper only. Going live is the operator's decision.
- After each ticket, the worker sends the head a short report: what was built or run, the numbers,
  and the next step it proposes. The head decides the next cell; workers do not choose children.

## 8. Data sourcing decision (operator, 2026-09-25)

The operator ruled that public market data may be collected from the internet for this program's
private, personal, non-commercial research, accepting the terms-of-use risk of the source sites
(including NSE's clause against automated collection), and that no data is purchased. Workers
follow these rules while collecting:
- Only files and pages that are publicly posted for download (daily bhavcopies, corporate-action
  and index-change notices, published constituent files). Never defeat an access control: no
  disguised user agents to get past a block, no captcha or bot-protection bypass, no logins.
  If a source blocks automated access, move to another public source (BSE, niftyindices.com,
  public datasets) and report it.
- Polite: one request at a time, a few seconds apart, resumable, no retry storms; stop on 401,
  403 or 429.
- Never from the production host or its broker-registered static IP (EM-211, EM-218): a block
  must not touch order placement. Run collection from the development machine.
- Record the source URL and fetch date for every file; the data stays local and is not shared.
