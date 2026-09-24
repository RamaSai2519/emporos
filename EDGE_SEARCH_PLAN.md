# EDGE_SEARCH_PLAN — find a cost-adjusted intraday edge for ₹50,000

**Jira:** EM-191 (epic). **Follows:** EM-176, which is Done. EM-176 built the machinery and validated nothing.
**Audience:** an autonomous coding agent that runs this plan in a loop, one iteration per wake-up,
plus the operator who reviews what it hands back.
**Status of this file:** plan of record. The agent must not edit the numbers in §3 or §6.
The operator may change them, in a commit that says why.

---

## 0. Mission, and what "done" means

Find one NSE cash-equity intraday strategy, or a portfolio of strategies, that makes money after
every real cost on ₹50,000 of capital. Prove it with evidence the platform already trusts:
the unchanged gates in `config/robustness/benchmark.yaml`, a one-shot vault holdout, and forward
paper trading under `config/parity.yaml`.

The agent keeps going. It works through every lane in §5. It generates child hypotheses from what
each failure teaches (§7.4). It opens new data frontiers (§4) when the current data is used up.
Only two states end the loop:

| Terminal state | Condition | What the agent delivers |
|---|---|---|
| **SUCCESS** | A frozen candidate passes stages S4, S5 and S6 in §6. | A graduation dossier (§9.1). EM-191 goes to Done. Live trading stays **off**: turning it on is the operator's call through EM-189 and EM-190. |
| **EXHAUSTED** (a pause, not a quit) | Every cell in the search map (§5) is terminal: REJECTED with a published report, or BLOCKED on a named external dependency that has its own Jira ticket. | A negative-result report (§9.2) and a ranked list of next frontiers, each with a ticket. The agent then stops until the operator unblocks a frontier or adds lanes. After that it resumes. |

**Why the loop is not "don't stop until profitable":** if the rule were "run trials until one passes",
the loop would always end with a pass. With enough trials, luck alone produces a strategy that clears
any fixed threshold. That result would lose the ₹50,000 in live trading. The defences in §3 exist so
that a SUCCESS here actually means something. **An edge the agent can only reach by breaking §3 is not
an edge.** "Not found yet, and here is exactly what was ruled out" is a real result. A result
manufactured by breaking the rules is not.

---

## 1. What EM-176 established. Start here and do not re-derive it.

| Evidence | Finding |
|---|---|
| 11 strategies from EM-114, each with its own card (`docs/strategies/comparison.md`, `benchmark_50k/`) | All REJECTED. **Gross P&L was negative for every one**, before any cost. Every one still loses with zero slippage. An always-long baseline beat all of them. |
| `momentum_v1` (`docs/backtests/README.md`) | −28.4% over one year. Profit factor 0.34. |
| `liquidity_thrust_v1` from EM-171, 2022-08 to 2026-09 | REJECTED with 1,134 trades. Gross was negative. |
| EM-178 feature engine (`docs/research/feature-engine.md`) | No feature carried net edge. |
| EM-179 cross-sectional residual momentum | Real gross edge in TRAIN: +0.023%/trade at t=3.9. Costs were about 0.2%/trade, **roughly 10× the edge**. The pattern was also unstable out of sample. |
| EM-180 lead-lag | The single cell that was net-positive in TRAIN, NSE:25 (15m early move predicting the late-session return, net +0.59%), came out at **−0.21% in the holdout**. That is the look-elsewhere effect in practice. |
| EM-181 order flow | OHLCV proxies only. No depth or L1 history exists. No net edge. |
| EM-187 Jev (LLM) | The harness works. The Jev arm was rejected. |

### 1.1 The cost wall, computed from `config/fees/angelone-2026-09-20.yaml`

The table shows round-trip cost as a percentage of position value. The "benchmark" column includes
5 bps of slippage on each side. The "adverse" column uses 1.5× fees and 15 bps of slippage on each side.

| Position value | Charges only | Benchmark break-even | Adverse break-even |
|---|---|---|---|
| ₹5,000 – ₹20,000 | 0.271% | **0.371%** | 0.707% |
| ₹25,000 (current `max_position_value`) | 0.224% | **0.324%** | 0.636% |
| ₹50,000 | 0.130% | 0.230% | 0.495% |
| ₹1,00,000 (needs leverage) | 0.083% | 0.183% | 0.424% |
| ₹2,50,000 (needs leverage) | 0.054% | 0.154% | 0.382% |

Up to ₹20,000 the 0.1% brokerage rate applies, so the percentage cost is flat. Above that the ₹20
per-order cap kicks in and the percentage falls.

**What this means for the search:**

1. Under current risk limits, a trade must gain at least **~0.33% gross on average**, and **~0.64%** to
   survive the adverse scenario. A typical intraday signal in the literature makes 0.02–0.10%.
   The search must look for **few, large, high-conviction moves**, not frequent small ones.
2. Position size is the single biggest cost lever. At ₹25,000 the charges are half what they are at
   ₹5,000. Every hypothesis is sized at the largest value `config/risk.yaml` allows. The agent may
   also *research* larger, leveraged sizes (§5 lane L1). Actually trading at those sizes needs the
   operator to change `risk.yaml` (§8).
3. At ₹25,000 and above, slippage is about a third of benchmark cost. Better execution under the
   limit-only constraint is a lane of its own (L13).

### 1.2 Data on hand

- **Instruments and bars:** 29 NSE instruments, 2016-10-03 to 2026-09-18 (EM-132). 5-minute bars,
  plus 1-minute bars for recent history. Daily bars are in the local Parquet cache at
  `~/.cache/emporos/candles/{5m,1d}` (31 and 29 instruments). Read through `FileCandleReader`, which
  involves no Mongo and no network.
- **Missing:** L1 or depth history, a sector taxonomy, split/dividend adjustment (quarantine only),
  index series, India VIX, an event calendar.
- **Known gaps:** 15:15–15:29 is missing in the most recent ~34 sessions. Seven quarantined
  corporate-action days (`docs/data/known-limitations.md`).
- **Instrument master:** no history before 2026-09-19, so any earlier run needs `--assume-current-universe`.
- **Fee schedule:** read on 2026-09-20 and not reconciled against a contract note.

---

## 2. Operating rules. These bind every iteration.

All of `AGENTS.md` applies. In particular:

- **Jira first.** Each lane or infrastructure step gets an EM subtask under EM-191. Create it in
  To Do, move it to In Progress when work starts, and move it to Done only after a commit. Every
  commit message starts `EM-<n>: `. If the Atlassian MCP is unavailable, stop and ask the operator.
  Do not keep a local substitute backlog.
- **OOP and SOLID in all research code.** New strategies live in `emporos.strategies`, which is pure
  and may import only `domain` and `broker.base`. New research engines live in `emporos.research`.
  New orchestration that runs backtests lives in `emporos.backtest`. `opportunity` never imports
  `backtest`. Every public type is a frozen dataclass or a `Protocol`.
- **Definition of done for every commit:** `lint`, `typecheck`, `test`, `coverage-gate` and
  `lint-imports` all pass.
- **Keep load off the shared Atlas cluster.** Research reads the local Parquet cache. New history is
  fetched once, in throttled batches, into Atlas and the cache, and is never swept repeatedly.
  Never run a whole-directory `-m integration` suite. Never use `mongomock`.
- **No live orders, ever.** This program uses backtest and paper trading only. The graduation gate
  must keep refusing live trading.
- **Existing engines only for anything that produces a verdict.** Verdicts come from
  `emporos backtest curate` or `emporos.research` studies, using the dated fee schedule and
  `RiskRuleGate`. The fast screener (§6, stage S2) is for triage only and can never produce a verdict.
- **Never regenerate goldens to turn a build green.** Never overwrite a published experiment report.

---

## 3. Anti-self-deception rules. Fixed. The agent must not relax them.

1. **Declare before looking.** Every hypothesis gets `config/experiments/<slug>.yaml` (EM-188
   format). It states the economic rationale, the exact falsification test and the full parameter
   grid. It is **committed before** any data run. `--allow-uncommitted-declaration` is forbidden.
2. **Count every trial.** The global trial count N is the number of rows in the experiment registry
   plus every screener evaluation (§6, S2). EM-114 to EM-187 trials count too. The Deflated Sharpe
   Ratio (DSR) at S4 and S5 uses the program-wide N, not the per-experiment N.
3. **Frozen thresholds.** `benchmark.yaml`, `parity.yaml`, `graduation.yaml` and `risk.yaml` are
   read-only for the agent. So are the S2 and S3 bars in §6. A near-miss is a REJECT. The agent may
   *propose* a new benchmark to the operator in a ticket. It never runs one it wrote itself.
4. **Economic rationale is required.** Every hypothesis must name who is on the other side of the
   trade and why they lose: forced flows, liquidity provision, behavioural bias, information delay.
   "The backtest says so" is not a rationale.
5. **Check feasibility before running anything.** Using TRAIN-period data only, the conditioning set
   must show a median absolute move over the holding horizon of at least **2× the adverse
   break-even** at the declared size. That is ≥1.3% at ₹25,000. Below that, the hypothesis cannot
   pay its costs, whatever its hit rate. Record it as `INFEASIBLE`. This still counts as a trial.
6. **One-shot vault (§4.3).** Only S5 may read the vault, for one frozen candidate. The whole program
   gets **at most 3 vault opens**. After the third, the vault is burned and only forward paper
   trading counts as clean evidence.
7. **Children come from TRAIN diagnostics only.** A child hypothesis (§7.4) may be motivated only by
   the parent's TRAIN or walk-forward results, never by its holdout or vault numbers. Flipping a
   rejected signal's direction is a new hypothesis. It needs its own declaration and its own
   rationale, and it counts as a trial.
8. **Robust or nothing.** A candidate that only works at one parameter point, in one year, on one
   instrument, or in one month fails the existing perturbation and concentration gates. Treat that
   as a finding. Do not tune around it.

---

## 4. Stage 0: foundations. Build once, before the lanes.

Each step below is its own EM subtask. They unlock lanes. They are not optional polish.

### 4.1 Tooling
- **F1. Search map and state.** Add `docs/research/edge-search/search-map.yaml`. Each cell has:
  id, lane, hypothesis slug, parent, status (one of `TODO`, `INFEASIBLE`, `SCREEN_REJECT`,
  `CONFIRM_REJECT`, `CURATE_REJECT`, `VAULT_REJECT`, `PAPER_REJECT`, `BLOCKED(<EM-key>)`,
  `CANDIDATE`, `VALIDATED`), experiment ids, and a one-line lesson.
  Also add `STATE.md`, a short human-readable resume point: current cell, global N, vault opens used,
  last commit. Put a small typed loader and validator in `emporos.research`, with tests, so a
  malformed map fails CI.
- **F2. Global trial counter.** Add a registry query that returns program-wide N. The S4 and S5 DSR
  calls take N from it. Tests prove that no call site can pass a local N.
- **F3. Fast screener.** Add a vectorised signal → forward-return → net-P&L evaluator over the Parquet
  cache, in `emporos.research`. It must use `research.costs.TransactionCostModel` at the declared
  position size. **Parity test:** on three existing strategies, screener net expectancy per trade
  must match `backtest curate` within 10% relative, or within 0.02% absolute where the value is near
  zero. Until that test passes, the screener result is only advisory. Each screen writes one
  registry row, so it counts toward N.
- **F4. Size-aware evaluation.** Every study and curation takes the declared position value,
  defaulting to `max_position_value`, instead of a fraction chosen after the fact.

### 4.2 Data frontiers. Each opens lanes. Order by how many lanes it unlocks.
- **D1. Wider universe.** Fetch, throttled and resumable, as far back as the broker allows:
  1-minute and 5-minute history for NIFTY 100, then the liquid part of NIFTY 200. Use
  `emporos history fetch-bars`, respect the 403 rate limit, run it in the background, then mirror
  into the Parquet cache. Keep the historical as-of universe rules (EM-177) in force.
- **D2. Index and volatility series.** NIFTY 50, NIFTY BANK, sector indices and INDIA VIX as candles.
  Use the index tokens from the Angel One scrip master. These unlock market-regime conditioning,
  beta hedging and lead-lag (L6, L7, L8).
- **D3. Sector taxonomy.** NSE industry classification per instrument, stored in versioned,
  append-only form. This replaces the `SectorClassifier` stand-in.
- **D4. Corporate-action adjustment.** Build an adjusted series from the quarantined findings plus a
  corporate-action source. Keep raw prices for fills and adjusted prices for features.
- **D5. Event calendar.** Earnings dates and board meetings from NSE corporate announcements (the
  screener MCP `get_company_announcements` is one source), plus index-rebalance dates, F&O ban list,
  bulk and block deals, and ex-dates. Every event is timestamped with **when it became public**, not
  when it happened, so lanes cannot leak future information.
- **D6. Daily NSE bhavcopy.** Delivery %, and daily FII/DII flows. Daily data, feature use only.
- **D7. Forward quote recording.** Starting now, record L1 quotes (bid, ask, sizes) for the research
  universe during market hours in the worker's read-only feed, and store them compactly (Parquet, not
  hot Mongo). Nothing is gained today. In 3–6 months this makes the microstructure lanes (L13, and
  the quote-conditioned part of L9) possible, and it measures real spreads for the cost model.
  **Start it early. Every day it isn't running is lost.**
- **D8. Cost model verification.** Needs the operator. Reconcile the fee schedule against one real
  contract note (EM-190). Until then every report carries `verified: false`.

Scraping NSE or other websites is allowed only within their terms of use. Anything needing a paid
feed (tick or depth vendors) is an operator decision (§8). File it as BLOCKED and move on.

### 4.3 Data partition, sealed before the first lane runs
| Split | Range | Instruments | Who may read it |
|---|---|---|---|
| **Discovery** | start of data → 2024-12-31 | all non-vault | S1–S3, walk-forward |
| **Confirmation** | 2025-01-01 → 2026-03-18 | all non-vault | S3 (the declared holdout) and S4 (walk-forward test windows) |
| **Vault** | 2026-03-19 → 2026-09-18 | **sealed instrument set** (below) | S5 only |
| **Forward** | from the day a candidate is frozen | the candidate's universe | S6, paper trading |

- The last year has been heavily used by EM-114 and EM-179 to EM-181, so time alone is not clean.
  The vault is also **sealed by instrument**. From the D1 names that no earlier research has loaded,
  pick 30% at random with a committed seed, and record their ids plus a content hash in
  `docs/research/edge-search/vault.yaml`. Build a `VaultGate` in `emporos.backtest.integrity` style
  that refuses any read of those instrument/date ranges unless a committed unseal record exists
  (candidate hash, reason, open number 1–3). Test it the same way `test_look_ahead.py` tests
  look-ahead.
- Until D1 lands, the vault is time-only over the 29 current names. Record this as a known
  weakness, and give extra weight to forward paper results.

---

## 5. The search map. Every lane, every cell.

Each lane is a family. The agent expands a lane into cells, one declaration each, bounded by the
grid it declares. **Priority (§7.2) follows §1.1: low turnover and large moves first.**
Cells marked (D#) wait for that data frontier.

**P1 lanes (large moves, low turnover, most likely to clear the cost wall)**

- **L1 Size and cost frontier (meta-lane, applies to all others).** For every CANDIDATE-or-better
  cell, re-evaluate it at ₹25k (current cap), ₹50k (all capital in one name), and at ₹1L and ₹2.5L
  using MIS leverage. Also try at most 1 and at most 2 trades per day. Results above ₹50k are marked
  *requires operator risk change* (§8) and cannot reach SUCCESS without that change.
- **L2 Event-driven days (D5).** Post-earnings drift from the first 30–60 minutes of the reaction
  day. Earnings-day gap continuation vs fade, bucketed by surprise proxy (gap size ÷ ATR) and
  relative volume. Index inclusion/exclusion effective-date flows. Ex-date behaviour. Bulk or block
  deal follow-through on the next session. F&O-ban entry and exit days. Rationale: forced or
  informational flows arrive in bursts, and slow capital moves late.
- **L3 Gaps and the opening hour.** Idiosyncratic gap (stock gap minus index gap, D2) × size bucket
  × previous-day range × relative volume. Opening-range breakouts restricted to top-decile relative
  volume and wide-range days. First-hour reversal after an overnight shock. Hold to the close
  (single trade) vs a time stop. Rationale: overnight information is priced over the first hour.
  Retail momentum chasing and liquidity-provider inventory both show up there.
- **L4 Volatility regime and range expansion (D2).** NR4/NR7 or inside-day breakouts on the next
  session. ATR-percentile regimes. INDIA VIX level and change. Allow trading only on days whose
  expected range clears the feasibility gate (§3.5). Rationale: trade only when moves are big
  enough to pay the costs.
- **L5 Full-session trend days.** Classify a trend day early (by 10:15), using the index trend,
  sector breadth (D3), and relative volume, then hold the leaders until 15:10. One trade per day at
  the maximum size.

**P2 lanes (filters and combinations that raise gross per trade)**

- **L6 Meta-labelling and filtering existing signals.** Take the 14 rejected strategies' raw entries
  as primary signals. Train a secondary classifier (logistic or gradient-boosted, both regularised)
  on TRAIN-only features: regime, relative volume, time of day, index state, event proximity. The
  classifier picks the top slice of trades whose expected gross clears break-even. Use purged
  k-fold with an embargo (López de Prado). Every model configuration counts as a trial.
- **L7 Market and sector lead-lag, done properly (D2, D3).** Index futures or NIFTY leading
  constituents. Sector index leading laggard members. Pre-declare **one** cell at a time, as EM-180's
  finding requires. No mining the grid.
- **L8 Cross-sectional long/short with low turnover (D1, D3).** Residual momentum and residual
  reversal (EM-179's gross edge) rebuilt for turnover: rebalance once or twice a day, top/bottom
  single names only, and a hurdle so a rank change must be large enough to justify trading.
  Market- and sector-neutral pairs, including sector-mate pair z-score reversion. Intraday shorts
  are allowed in cash equity as MIS. Assess the size cost of two legs honestly.
- **L9 Volume and participation.** Relative volume by time of day. Volume-price divergence.
  Accumulation/distribution from delivery % (D6) as a daily prior. Opening volume shock.
  Quote-conditioned variants wait for D7.
- **L10 Ensembles and portfolios.** Only for components that are each net-positive at S3 but too
  thin alone. Build equal-risk combinations of weakly correlated components. The combination is
  declared **before** components are chosen, via a rule such as "all S3-passing components from lanes
  X, Y". Choosing the best-looking components after the fact is not allowed.

**P3 lanes (breadth, lower prior; run them so the search is complete)**

- **L11 Classic indicators, systematically.** Families: MA and EMA crosses, MACD, ADX/DMI,
  Supertrend, Parabolic SAR, Bollinger, Keltner, RSI(2 to 14), Stochastic, Williams %R, CCI, MFI,
  OBV, VWAP bands, Ichimoku, Heikin-Ashi, pivots and CPR, Donchian, Aroon. Run each **as a
  feature** through the EM-178 engine first: does it predict forward return net of costs at the
  declared size? Only features that pass S2 become strategies. This is how "every indicator" gets
  covered without inflating N one strategy at a time.
- **L12 Multi-timeframe.** Daily trend or state (1d cache) as the regime, with intraday entry.
  15m and 60m confirmation on 5m entries.
- **L13 Execution alpha (limit-only).** Passive limit entries at VWAP, mid, or the previous bar's low
  and high, instead of marketable limits. The fill model must be conservative: fill only if a later
  bar trades *through* the limit by at least one tick, and count missed fills as missed trades, not
  as free options. Quote-based validation waits for D7. Paper trading measures the truth.
- **L14 Calendar and seasonality.** Day of week, month-end and month-start, pre-holiday, weekly
  expiry day (use the expiry weekday in force on each date, since it changed in 2025), results-season
  weeks.
- **L15 Machine-learning return prediction.** Gradient-boosted or linear models on the full feature
  set. Purged and embargoed CV. Nested hyperparameter search, where every configuration counts
  toward N. Output is a ranked signal that must then pass S2 to S6 like any other. Deep models only
  once D1 has made the dataset big enough to support them. Record the number of rows per parameter.
- **L16 Jev as a filter.** EM-187 found the arm itself rejected. Revisit it only as an L6-style
  meta-feature on a CANDIDATE from another lane, with the knowledge-cutoff guard kept on.

**Out of scope. The agent files an escalation and does not attempt these.** Futures and options,
BTST, swing or delivery trading, and any market or product outside NSE/BSE cash-equity intraday.
If the EXHAUSTED report concludes the edge is not in intraday cash equity at this capital, it says
so plainly and lists these as the frontiers.

---

## 6. Promotion ladder. Fixed thresholds, all applied in order.

| Stage | Data | Pass bar (all conditions must hold) | On failure |
|---|---|---|---|
| **S1 Feasibility** | Discovery | §3.5 median-move gate at the declared size | `INFEASIBLE` |
| **S2 Screen** (F3 screener) | Discovery | Net expectancy > 0 at benchmark costs **and** gross per trade ≥ the adverse break-even. Net t-stat ≥ **3.0** (Harvey–Liu–Zhu hurdle for new factors). ≥ **300** trades. Net positive in ≥ **60%** of calendar years. No single instrument > 50% of net. | `SCREEN_REJECT` |
| **S3 Confirm** | Confirmation (declared holdout) | Net > 0 under the **adverse** scenario. Same sign as S2. Net t ≥ **2.0**. ≥ **100** trades. | `CONFIRM_REJECT` |
| **S4 Curate** | Discovery + Confirmation, walk-forward | Every gate in `benchmark.yaml`, verdict **VALIDATED**: P(net>0) ≥ 0.95, ≥150 trades, ≥500 days, ≥3 windows, ≥2 regimes, ≥60% positive windows, window DD ≤10%, **DSR ≥ 0.95 using program-wide N**, concentration, perturbation neighbours ≥50%. PBO (EM-182) < 0.2. Run through `emporos backtest curate --declaration`. | `CURATE_REJECT` |
| **S5 Vault** | Vault (one open) | Candidate frozen: config hash and code commit recorded. Net > 0 under adverse. P(net>0) ≥ 0.90 by the benchmark Monte Carlo, **or** fewer than 30 vault trades plus net > 0, which is then marked "thin; paper must carry it". No parameter changes are allowed afterwards. | `VAULT_REJECT`. A child may be declared only per §3.7. |
| **S6 Paper** | Forward | `emporos worker run` in paper mode. Every `parity.yaml` gate. At least `min_sessions` (10) **and** at least 30 matched trades. Cumulative net > 0. Realised expectancy inside the backtest's 90% prediction interval. | `PAPER_REJECT` |

When S6 passes, the result is **SUCCESS**. Write the dossier (§9.1). Do not enable live trading.

---

## 7. The loop: one iteration, fully specified

Each wake-up of the agent is one iteration. An iteration must finish within one context window.
If it cannot, split the cell.

### 7.1 Resume
1. Read this file, `docs/research/edge-search/STATE.md` and `search-map.yaml`. Run `git status` and
   `git log -5`. Query Jira for EM-191's open subtasks.
2. If there is uncommitted work, finish it or revert it. Never leave it half-done.
3. If S6 paper trading is running, check today's parity report first.

### 7.2 Choose
Choose the next `TODO` cell in this order:
- (a) Any foundation step (§4) that blocks the most TODO cells.
- (b) The highest-priority lane: P1 before P2 before P3.
- (c) Within a lane, the cell with the highest TRAIN-only prior. Children of near-miss parents come
  first.

Foundations and lanes may interleave. Once D7 is useful, it keeps running in the background.

### 7.3 Execute the cell
1. Create or reuse the lane's EM subtask and move it to In Progress.
2. Write the declaration (rationale, falsification, grid, feature versions, declared size) and
   commit it.
3. Run S1. Then, if it passes, S2, then S3, then S4, running each stage only while the previous one
   passes. For S5 and S6, stop and follow §6. S5 uses a vault open. S6 runs over real sessions.
4. Publish the experiment report (automatic in `curate`/`study`). Update the cell's status, its
   experiment ids and its **one-line lesson** in `search-map.yaml`. Update N and `STATE.md`.
5. Run the five DoD gates. Commit as `EM-<n>: <cell>: <verdict>`. Transition the Jira subtask when
   the lane is complete.

### 7.4 Recurse: failures generate children
Diagnose each rejected cell **from TRAIN or walk-forward output only**. Spawn children (new cells,
status `TODO`, `parent` set) according to the failure mode:

| Failure mode | Child hypotheses to declare |
|---|---|
| Gross positive, net negative | Raise the size (L1). Cut turnover: fewer trades, stricter filters (L6). Hold longer, up to the full session. Passive entry (L13). Restrict to higher-volatility names or days (L4). |
| Gross negative | Is there a *mechanism* for the opposite direction? If yes, declare it with a rationale. If no, mark the lane lesson "no signal" and spawn nothing. |
| Unstable across years or regimes | Condition on the regime that TRAIN shows it works in (declared regime-specific, per the `benchmark.yaml` min_regimes note). |
| Concentrated in 1–2 instruments | Test the mechanism on D1's wider universe. Do not trade the single name. |
| Too few trades | Widen the universe (D1) before loosening any condition. |
| Works at one parameter point only | No child. That is overfitting. |

Every child is a new trial. A lane is finished when its cells and all their descendants are terminal.

### 7.5 Meta-review every 10 iterations
Write `docs/research/edge-search/review-<k>.md` covering:
- The lessons so far.
- Which lanes are converging and which are dead.
- What global N now implies for the DSR hurdle.
- Whether the priority order should change. Changes must be justified from the lessons, never from
  a vault or holdout number.

Add a comment on EM-191 linking the review.

### 7.6 End conditions
- **SUCCESS:** §9.1.
- **EXHAUSTED:** §9.2.
- **Blocked on the operator** (Atlassian MCP down, credentials needed, a paid-data decision): comment
  on the ticket, write down precisely what is needed in `STATE.md`, and continue any other unblocked
  lane. Stop only if **nothing** is unblocked.

---

## 8. Escalations. Operator decisions the agent must not take itself.

File each one as an EM ticket, set the dependent cells to `BLOCKED(<key>)`, and keep working on
other lanes.

| Decision | Why the agent can't make it |
|---|---|
| Raise `max_position_value` / `max_capital_deployed` or use MIS leverage, for L1 results above ₹25k | Real-money risk limits (EM-189 approval). |
| Buy tick or depth data, or subscribe to a vendor feed | Money, and a licence. |
| Add a new benchmark, or change the parity or graduation thresholds | These define what "validated" means. |
| Move into futures, options, BTST, swing or another venue | Product scope (plan.md). |
| Contract-note fee reconciliation (D8), static IP, a live order round trip (EM-190) | Human-only actions. |
| Vault open 3 of 3 | The last clean historical test. The operator must see the case first. |

---

## 9. Deliverables

### 9.1 SUCCESS dossier: `docs/research/edge-search/DOSSIER.md`
- The frozen config hash and commit.
- The declaration, S2 through S6 reports with links, and the cost breakdown under every
  `cost_scenarios` row.
- Global N and the DSR that N implies.
- Vault opens used.
- The paper parity report.
- Capacity: slippage at 2× size.
- Failure modes and kill criteria: the live drawdown or expectancy drift that should halt it,
  mapped to the EM-189 tripwires.
- The EM-189 promotion checklist with each item's state.

Move EM-191 to Done after the commit. **Live trading is not enabled.**

### 9.2 EXHAUSTED report: `docs/research/edge-search/EXHAUSTED.md`
- Every lane, how many cells it had, and its terminal lesson.
- Global N.
- The best near-misses, with the reason each failed.
- An honest conclusion on where the cost wall sits for ₹50k intraday cash equity.
- The next frontiers, ranked by expected value, each with its EM ticket: data to buy, leverage
  decision, product scope.

Comment on EM-191, leave it In Progress, and wait.

---

## 10. How to run the agent

Run it self-paced (for example `/loop` without an interval), with this prompt:

> Follow `EDGE_SEARCH_PLAN.md` exactly. Resume from `docs/research/edge-search/STATE.md`. Do one
> iteration (§7). Never change the §3 and §6 thresholds, never open the vault outside S5, never
> place a live order. Commit, update Jira and STATE.md, then schedule the next iteration. Stop only
> on SUCCESS, EXHAUSTED, or when every remaining lane is blocked on the operator.

The first iterations are foundations F1–F4, D1 (in the background), D2, and the vault seal (§4.3).
Start D7 quote recording as soon as possible, because its value grows with time. Do not run any
lane before F1, F2 and the vault seal are committed.
