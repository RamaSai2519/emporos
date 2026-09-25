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
- Months (amended 2026-09-25, see note): ≥ 60% of months WITH ANY EXPOSURE net positive **and**
  ≤ 40% of ALL months net negative; worst month ≥ -10%; max drawdown ≤ 25%.
- Monthly-return t-stat ≥ 2.5; ≥ 100 round trips; positive in ≥ 60% of calendar years.
- No instrument > 25% of net profit (Track A).
- Parameter neighbours: ≥ 50% of declared adjacent arms also net positive at benchmark.

  *Amendment note (head, operator-delegated, 2026-09-25, before A4 ran):* the original bar, ≥ 60%
  of ALL calendar months net positive, counted an all-cash month as a failure, contradicting §9
  (standing aside is a correct outcome), which was written before any result. The amended bar
  still counts every losing month against a strategy, and a book cannot pass by sitting in cash.
  It changes no recorded verdict: every rejected Track A/B arm also fails another §3.2 bar.
  The single-name concentration check applies to single stocks; a broad index ETF is exempt
  (its share is still reported).

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

## 9. Operating model (operator, 2026-09-25): a combination, not one strategy

The program is building a book of several sleeves plus a daily allocator, not a single strategy.
The platform (delivery, F&O, multi-day risk) is built only after research has found the sleeves.
Every sleeve is designed and judged as a component of this book:

- **Three postures per day, decided by rules declared in advance:**
  - **Hold** (cash): no sleeve has a qualifying signal, or a risk rule (§3.5) is tripped. Expected
    to be common, and a correct outcome, not a failure.
  - **Normal:** a sleeve's signal qualifies, sized at its declared base size.
  - **Aggressive:** only when conviction is high by an OBJECTIVE, pre-declared measure. Examples:
    the signal is in its top decile of historical strength, two independent sleeves agree, and the
    regime filter is favourable. Size steps up (for example 1.5-2x) within §3.4's ruin limit.
    "Sure" is never a feeling: the conviction rule is part of the declaration, and the aggressive
    tier is screened as its own arm, so it has to earn its size in Discovery.
- **Small intraday trades (scalping) on quiet days** are a candidate sleeve, subject to the same
  cost wall as Track C: at Rs 50,000 a round trip costs about 0.23-0.5%, so a scalp must plausibly
  clear that per trade. It is researched under Track C; it earns a place only by passing S2.
- **Each sleeve reports its daily P&L series,** so the combination (Track A4, EDGE_SEARCH_PLAN L10)
  can be built from correlations and the §3.2 monthly bars applied to the whole book.

## 10. Operator decision (2026-09-25, after review 3): core + satellites, and keep searching

**Core (Track A-core).** A survivorship-free, low-turnover ETF core is the base of the book. Its bar
is set here, before any core cell runs, and is not the §3.2 satellite bar. On Discovery, all must
hold:
- Net CAGR ≥ the 60/40 NIFTYBEES/GOLDBEES buy-and-hold (yearly rebalance, same costs, same days),
  and not more than 2 points below it in either half of the window.
- Max drawdown ≤ 0.6 × that benchmark's; worst month ≥ -10%; the amended §3.2 months bar; monthly
  t ≥ 2.5; §3.4 P(drawdown ≥ 30%) ≤ 5%. Adverse-cost CAGR > 0.
- Round trips are not required: a monthly allocation trades rarely, and monthly t carries the
  statistical burden.
Then S3, S4 and paper as §3.3. Core cells fill at the next session's CLOSE, not its open: early ETF
opening prints are noisy (EM-233, etf-bars-report.yaml).

**Satellites** must beat the core on net Sharpe over the same days to earn capital.

## 11. Track T — intraday triggers with a calibrated probability filter and Jev (operator, 2026-09-25)

The operator's direction: find triggers, take everything calculable into account, and trade only
when the probability is high; use Jev, live, in the backtest. Design:
1. **Triggers** (primary events): the program's intraday scans on D1 (gap + first-hour retrace,
   the daily top shock, ORB with volume, VWAP and RSI reversion, NR7, the index-trend leader, the
   in-session results jump). Each gives a candidate trade with a side, the next-bar entry and the
   15:15 exit, through the shared order path.
2. **Features at the trigger, as-of only:** the trigger's own measures, returns and volatility of
   the name over several horizons, volume versus its time-of-day norm, distance to VWAP, the NIFTY,
   sector and INDIA VIX state, breadth across D1, time of day, weekday, days to expiry and to or
   from the name's results, and liquidity. No symbol and no date.
3. **Label and probability:** P(net P&L at benchmark costs > 0). A model trained walk-forward by
   year (train on earlier years, predict the next, one-day purge), then calibrated. **Trade only if
   the calibrated P ≥ the declared threshold (operator: 0.80) AND the expected net per trade at
   ADVERSE costs > 0** (a high win rate alone is not an edge: B1 won 82% of its trades and lost).
   Calibration is reported and judged: if trades scored ≥ 0.80 win clearly less often
   out-of-sample, the model is rejected, whatever its P&L.
4. **Jev (live, `gpt-4o-mini`, declared cutoff 2023-10-01):** for each trade that passes step 3, Jev
   receives the anonymised trigger and features and confirms or rejects. The knowledge-cutoff guard
   allows Jev only from 2024-01 on (cutoff plus 90 days), so the Jev arm is backtested on 2024
   Discovery and later splits only. Before that date the model may remember the market, and no run
   is allowed. Jev's value is judged as an increment over step 3 alone (docs/research/jev-incremental.md),
   net of its token cost, and every call is recorded to the journal so replays are free.
5. **Bars:** EDGE_SEARCH_PLAN §6 S2 on the pooled out-of-sample predictions, then S3 and onward.
   Hyperparameters are fixed in the declaration (no inner search); each model/threshold arm is one
   counted trial.

## 12. Track L — LLM-led trading, the higher-risk path (operator, 2026-09-25)

After Tracks A, B, C and the C1 core were rejected on their bars, the operator chose a riskier path:
let language models read the news and filings and make the trading decisions, behind a strong
code-enforced risk engine. Operator answers, recorded verbatim in substance:

| Question | Operator's answer |
|---|---|
| Money that may be lost before the experiment stops | **Rs 25,000** (of Rs 1,00,000) |
| LLM role | Decision maker (with a strong risk engine), event trader and a panel of LLMs, all three. Filtering our old triggers is not expected to help. |
| Models | Jev (`openai/gpt-4o-mini` via the gateway, free this week, no limit) and `gpt-4o` on the operator's OpenAI key (**USD 4 in credit: use sparingly**) |
| Validation | Post-cutoff backtest first, then paper, then live |
| Products | Intraday cash, swing (days to weeks), options buying |
| Loss limits | Rs 2,000 per cash trade; **Rs 5,000 per options trade** (premium is the whole risk); Rs 5,000 per day; Rs 25,000 in total |
| Approval | Fully automatic, options included; ask the operator only before staking more than 25% of capital at risk |
| Sources | NSE/BSE filings, news headlines, market context (numbers). Not social media. |

### 12.1 Why a post-cutoff backtest is honest here, and only here
Both models declare a knowledge cutoff of 2023-10-01 (gpt-4o-mini, and gpt-4o pinned to the
snapshot `gpt-4o-2024-08-06`). Neither can know prices or news after it. The guard's rule is kept:
nothing before **2024-01-01** (cutoff + 90 days). Before that date an LLM backtest measures the model's
memory, not its judgement, and no such run is allowed or reported.

Windows (the program's splits, unchanged):
- **Dev:** 2024-01-01..2024-12-31. Prompts, panel design and rules are developed here. Every
  variant run on Dev is one counted trial (`docs/research/profit/screens.jsonl`, track L), and at
  most **6 variants** are run before one is frozen.
- **Test:** 2025-01-01..2026-03-18 (Confirmation). **One run** of the frozen variant. Its result is
  the verdict. No prompt or rule changes after seeing it; a changed variant needs a new
  declaration and has no untouched window left except paper.
- **Vault** 2026-03-19..09-18 stays sealed. After Test, the next check is **forward paper trading**
  on live data (the D7 quote recorder and the live filings feed), then live under §12.5.

Company names MAY be shown to the models in this track (not anonymised): what a model knew of a
company before its cutoff was public at the decision time, and it cannot know outcomes after it.
The date guard, not anonymity, is what keeps this track honest.

### 12.2 Data (point-in-time, public, §8 rules)
- **Filings:** NSE and BSE corporate announcements for the D1 names (and the F&O stock list),
  2024-01-01..2026-03-18, with the exchange's broadcast timestamp, subject, category and the text of
  the attached PDF (extracted locally). Deduplicated across NSE/BSE.
- **Headlines:** public, dated news headlines naming a D1 company or the market. Each headline
  keeps its source's publication timestamp. A headline with only a date, no time, may inform a
  decision only from the NEXT session's open.
- **Market context:** from our own bars: the name's move since the previous close and since the
  event, VWAP distance, volume against its time-of-day norm, 5/20-day returns and volatility,
  NIFTY, the sector index, INDIA VIX, and F&O open interest where we have it.
- **Decision time** = max(event timestamp, the close of the last completed 5-minute bar) + **2
  minutes** of processing latency. Entry is the first bar that starts after the decision time. Nothing
  published after the decision time may reach the prompt (a test feeds later items and requires an
  identical prompt).

### 12.3 The decision pipeline (every call journalled; temperature 0; prompts versioned and hashed)
1. **Triage** (Jev, one call per event): is this material for the price, which direction, expected
   size and horizon (intraday / swing / none), whether the move is already in the price given the
   reaction so far, and a confidence of 0-100. Most events (board-meeting notices, trading-window
   closures, routine compliance) end here.
2. **Panel** (Jev, only for material events at or above the triage threshold): three independent
   personas: a **bull** analyst, a **bear** analyst, and a **tape reader** who asks what the price
   and volume already show. Each returns a direction, conviction and the instrument it would use.
3. **Judge** (Jev): reads the event, the context and the three views, and returns trade / no trade,
   the instrument (intraday cash, swing cash, call or put), the stop, the target and the holding
   period. A trade needs the judge's yes AND at least 2 of 3 panellists agreeing on direction.
4. **Arbiter** (`gpt-4o`, optional, scarce): on Dev it is tested ONCE, as a paired A/B on at most
   150 judge-approved candidates, spending at most USD 2.50, with the worst-case spend shown to the
   head first. It enters the frozen variant only if it improves Dev net P&L per trade after its
   cost.
5. **Daily posture** (Jev, once before the open, from the previous evening's headlines, global
   cues and the index/VIX state): hold / normal / aggressive. It scales size within the §12.4 caps
   and never overrides them.

The LLMs choose WHAT to trade and in which direction. Code decides HOW MUCH, enforces every limit,
and can always refuse. No LLM output reaches an order except through the risk engine.

### 12.4 The risk engine (code, not an LLM; the same rules in the backtest, paper and live)
- **Sizing:** cash quantity = floor(Rs 2,000 / |entry - stop|), and position value ≤ Rs 50,000
  intraday or Rs 25,000 per swing name. An option is sized so the premium paid ≤ Rs 5,000. A stop
  wider than 5% for intraday, or 12% for swing, is refused.
- **Daily loss** Rs 5,000 (realised plus open marked at stop): no new entries that day.
- **Total loss** Rs 25,000 from the start of the experiment: everything is closed and the track
  stops. Only the operator can restart it.
- **Open risk** (sum of stop losses plus premiums) never above Rs 25,000 (25% of capital). Above
  that, the system must ask the operator; with these caps that is not normally reachable.
- Max 3 intraday and 4 swing positions at once; no intraday entry after 14:45 and all intraday
  squared off at 15:15; limit orders only; no averaging down; one position per name; no naked
  option selling; stops are never widened.
- **Costs:** the program's cost schedules at benchmark AND adverse (§3.1; the intraday
  EDGE_SEARCH_PLAN costs), never loosened. Options: the Track B schedule, and premium fills use the
  day's settle/close price from the F&O bhavcopy, so **options are backtested on the swing horizon
  only** (a daily close entry and exit). Options on the intraday horizon can be tested only in paper.

### 12.5 Bars (fixed now, before any Dev run)
**Test window** (one run of the frozen variant), all must hold, net of costs AND of the LLM's
token cost:
- Net P&L > 0 at **adverse** costs; net expectancy per trade > 0 at benchmark costs.
- ≥ 60 trades; t of the daily net P&L ≥ 2.0.
- Max drawdown < **Rs 15,000** (60% of the loss budget), and the §3.4 bootstrap P(losing Rs 25,000
  within 12 months) ≤ 10%.
- ≥ 55% of months with trades net positive; no single name > 30% of net profit.
- The same pipeline with the LLM decisions replaced by a **coin flip** of the same trade count,
  instruments and risk engine (seeded, 1,000 runs) must be beaten with p < 0.05. This checks that
  the models, not the risk engine or the sizing, make the money.

**Paper** (after Test passes): ≥ 4 weeks and ≥ 30 closed trades, realised within the Test
backtest's 90% interval. **Live:** only under the §12.4 limits, starting at half size for the
first 2 weeks. The operator decides.

### 12.6 What to expect, said plainly
Prior that a frozen variant passes Test: about 10%. That is higher than the rule-based cells' prior
because reading text is what language models are good at and no earlier cell used text. It is still
low, because listed-company filings reach thousands of faster readers at the same second, and at
most one earlier cell (the results jump, L2) touched the same events: it found the news priced
within 5 minutes. The most likely place for an LLM edge is the SWING horizon: judging whether news
changes the business, which the market digests over days, not minutes.
