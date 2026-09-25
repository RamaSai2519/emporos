# Track R — the Driver Atlas: find what moves prices, then trade only what arrives early enough

**Owner:** head session (plans, decides, audits). **Workers:** Agent 1 (data), Agent 2 (analysis
engine, tests). **Operator direction, 2026-09-26:** stop fitting price patterns; work backwards from
every real move to what caused it (US market, the Fed, company actions, big groups such as Adani
moving a sector), and find the edge there, with LLMs, fully validated.
**Binds with:** PROFIT_PLAN §2 (declare before looking, count every trial, splits, costs never
loosened), §8 (data sourcing), §12 (Track L risk engine and costs).

## 0. The idea in one paragraph

Every earlier cell asked "does pattern X predict the price?". This track asks the reverse: for every
price move that mattered, **what was the cause, when did that cause become PUBLIC, and how much of
the move happened AFTER that moment?** Explaining a move after the fact is easy and worthless.
The edge, if one exists, is in causes that are **(1) observable, (2) whose direction is readable
at the moment they appear, and (3) whose price move is not finished by then.** The atlas measures
exactly that for every class of cause, and only the classes that pass become trading hypotheses.
Those are then tested on data the atlas never saw.

## 1. Splits (why this is not hindsight)

| Window | Use |
|---|---|
| **Atlas year: 2024-01-01..2024-12-31** | The whole discovery study: moves, causes, LLM attribution, the edge table. Directions and outcomes may be looked at here, because this IS the discovery sample. No hypothesis is ever judged on it. |
| **Back-test years: 2017-11..2023-12** | Out-of-sample in time for hypotheses that need NO LLM (numeric or calendar causes: US close, Fed dates, FII flows, group or peer moves, bulk deals, index events). The models' memory does not matter to them. |
| **Test: 2025-01-01..2026-03-18** | Out-of-sample for every hypothesis, and the ONLY one for LLM-read causes (text before 2024 is contaminated by the model's memory). One run per frozen hypothesis. |
| **Vault 2026-03-19..09-18** | Sealed, as always. Then paper, then live under §12 limits. |

At most **8 hypotheses** leave the atlas for testing, each a counted trial. The Track L Dev run
(PROFIT_PLAN §12) continues unchanged beside this track. It also uses 2024 for development and has
its own one-shot Test.

## 2. Phase 1 — the Move Ledger (numeric, no LLM; Agent 2)

For every D1 name (148) and every session:
- **Decomposition.** Daily return = beta_m × NIFTY + beta_s × its sector index + beta_g × its business
  group's equal-weight index (if in a group, §3.3) + a residual. Betas are rolling 120 sessions,
  estimated on data before the day. The same is done on 5-minute bars within the session (the
  market and sector components from the index bars).
- **A stock move event** is a session with |residual| ≥ 2.5 × its rolling 60-session residual σ, OR an
  intraday residual jump: a 15-minute window with |residual| ≥ 3σ of that name's 15-minute residuals.
- **A market move event** is a NIFTY session with |return| ≥ 1.2%, or an open gap ≥ 0.8%.
- **A sector move event** is a sector index session with |residual vs NIFTY| ≥ 2σ.
- For each event, record: the direction, the size (%, σ), the **onset** (the first 5-minute bar at which
  the day's cumulative residual passes 25% of its final value; overnight if the open gap alone is
  ≥ 50% of the move), the peak time, and the forward drift: the residual and raw return from the
  close over +1, +3, +5 and +10 sessions, plus the intraday path after onset (+15m, +60m, to 15:15).
- **Placebo sample:** an equal number of randomly drawn quiet name-sessions (|residual| < 0.5σ),
  seed 20260926, carried through every later phase.

Output: `docs/research/profit/atlas/move-ledger-2024.parquet` (+ 2017-2023 for the numeric
phases), with a report of the event counts per month, class and name.

## 3. Phase 2 — the Cause Ledger (point-in-time, public data; Agent 1)

Every candidate cause is stored with its **availability time**: when an ordinary retail participant
could first have known it (not when it happened). All sources follow §8 (public files only, polite,
no bypass, dev machine only, source URL and fetch date recorded).

### 3.1 Already held
NSE filings 2024-01..2026-03-18 (time to the second); 5-minute bars; NIFTY, sector indices and
INDIA VIX; stock and index F&O with OI; S&P 500, Nasdaq, USD/INR, Brent and the US 10-year (daily);
results dates (D5); index membership and change notices (A-F3); corporate actions.

### 3.2 To collect (in this order)
1. **Macro and event calendar** (hand-built YAML with a source per row, 2017-11..2026-03): FOMC
   decisions (federalreserve.gov; availability 23:30 or 00:00 IST, whichever applies with DST), US CPI and payrolls (BLS
   schedules; 18:00/19:00 IST), RBI MPC decisions (rbi.org.in; 10:00 IST), Union Budget, general and
   major state election results, India CPI/GDP releases, GST council meetings, SEBI board decisions
   affecting markets, NIFTY/Sensex and MSCI/FTSE index rebalancing announcement and effective
   dates, and the monthly and weekly F&O expiries.
2. **Institutional flows:** daily FII/FPI and DII net buy/sell (NSE/NSDL public reports; availability
   the same evening, about 18:00-20:00 IST, so usable from the NEXT open).
3. **Bulk and block deals** (NSE public historical archive; availability that evening), with the
   buyer/seller name, quantity and price.
4. **Insider and promoter trades** (SEBI PIT and SAST disclosures on the NSE filings feed and its
   archive): promoter buying/selling, pledge creation or release.
5. **Business-group map** (hand-built, with a source per row): Adani, Tata, Reliance, Bajaj,
   Aditya Birla, Mahindra, Murugappa, Jindal, Vedanta, HDFC, ICICI, and the other groups with at least
   2 D1 names. **Sector peer map:** the D3 industry map and the sector index map.
6. **Market headlines, date-level:** a public publisher's daily archive list (ET or Moneycontrol
   archive pages) for 2024 and the Test window, with the title and date only. **Used to ATTRIBUTE
   market and sector moves** (what the market was talking about that day), never as a
   trade trigger unless it is available before the next open.
7. **Asia session** (daily, Yahoo, as the global cues): Nikkei, Hang Seng, KOSPI, and the US index
   futures proxy if available. Asia closes during the Indian session, so availability = its close.

### 3.3 Availability rules (a test pins each)
- A filing: the exchange dissemination time. A macro release: its scheduled time. A US close: 01:30 or
  02:30 IST (DST). FII/DII, bulk and block deals: that evening, so the NEXT session's open.
- The group index and the peer set are built from membership as of the day before.
- Nothing is ever attributed to a cause whose availability time is AFTER the move's onset as a
  "cause that could have been traded". It may still be recorded as a cause, flagged `post_onset`,
  because the market can know things before they are public (a leak or anticipation), and that
  fraction is itself a finding.

## 4. Phase 3 — Attribution (LLM, gpt-4o-mini; Agent 2 builds, the head audits)

For each move event and each placebo sample, code assembles the candidate causes available in
[onset − 24 h, onset + 30 min] (and, for market events, the previous US session and the calendar),
plus the numbers: the decomposition, the onset, the peer and group moves, the sector and market
moves.

**Call A — attribution (sees the move):** pick the PRIMARY driver from the fixed taxonomy (§4.1),
optionally a secondary one, a confidence of 0-100, and a one-line reason citing the candidate item's
id. "Unexplained" is a valid and expected answer. Code, not the LLM, checks that the cited item exists
and records its availability relative to the onset.

**Call B — direction read (blind to the move):** a SEPARATE call that sees only the cause item
(the text or the calendar fact, with the numbers available at its availability time) and NOT the
price move. It says up / down / unclear, the expected horizon and a confidence. This is what a trader
would have known at the time. It is the call that decides whether a driver is tradeable.

### 4.1 Driver taxonomy (fixed now)
- **G Global:** G1 US equity overnight; G2 Fed/FOMC; G3 US data (CPI, payrolls); G4 crude oil;
  G5 USD/INR; G6 global risk-off / Asia session.
- **M Domestic macro and policy:** M1 RBI policy; M2 Budget or tax; M3 elections; M4 government or
  regulator action on a sector (SEBI, RBI circulars, PLI, bans, duties); M5 domestic data.
- **F Flows and supply:** F1 FII/DII flows; F2 index rebalancing or inclusion; F3 F&O expiry or
  rollover; F4 bulk or block deal; F5 promoter buy, sell or pledge; F6 new supply (QIP, OFS, IPO lock-in
  expiry).
- **C Company:** C1 results; C2 guidance or con-call commentary; C3 orders or contracts; C4 M&A or
  restructuring; C5 capital actions (buyback, dividend, split, bonus); C6 management change;
  C7 regulatory action, penalty or litigation; C8 rating change; C9 product approval (for example
  USFDA); C10 governance or fraud allegation or short report.
- **P Contagion:** P1 group company news (for example Adani); P2 sector peer news; P3 sector-wide
  move without own news.
- **U Unexplained.**

### 4.2 Attribution validation (all must hold before Phase 4 uses the atlas)
1. **Placebo:** on the quiet samples, Call A must answer "unexplained" or give confidence < 50 on ≥ 70%
   of them. If it confidently "explains" quiet days, it is story-telling and the atlas is void.
2. **Stability:** re-run Call A on a 20% sample with the candidate items shuffled and renamed: primary
   driver agreement ≥ 80%.
3. **Head audit:** I read 60 random attributions (stratified by class) against the raw items and
   grade each right, plausible or wrong. Wrong ≤ 15%.
4. **Cross-check:** where a calendar fact exists (a results filing on that day, an FOMC night, a block
   deal), agreement between that fact and the LLM's primary driver is reported per class.

## 5. Phase 4 — the Edge Table (the output that matters)

For every driver class (and for the main sub-cases the LLM or the data separates, such as C1 results
by beat or miss in the blind read), over the 2024 events:
- **n** events and the share of all moves it explains (the "what moves prices" answer).
- **Anticipated share:** the fraction of the move completed BEFORE the cause was public (leaks,
  pre-positioning).
- **Instant share:** the fraction completed within 15 minutes of availability (priced by
  faster participants; untradeable for us).
- **Tradeable remainder:** the signed move from availability + 15 min (intraday) or from the next open
  (overnight causes) to the horizon end (15:15, +1, +3, +5, +10 sessions), in the direction of the
  BLIND read (Call B), not the known outcome.
- **Base-rate control:** the same remainder measured for ALL occurrences of that cause (every filing of
  that category, every FOMC night, every block deal), not only those that turned into big moves.
  **This is the critical anti-hindsight step:** the ledger starts from moves, but a trader sees every
  cause and does not know which will turn into a move. Only the all-occurrences figure is tradeable.
- A t-statistic on day-clustered errors, and the cost hurdle: 2 × adverse round trip for the horizon.

**A driver class becomes a hypothesis only if**, on ALL occurrences in 2024: n ≥ 40; mean tradeable
remainder in the blind direction ≥ 2 × the adverse round-trip cost; clustered t ≥ 3; ≥ 60% of
months positive; and the Call B direction read has confidence ≥ 60 on at least half the cases.
At most 8 classes, ranked by t, go forward. If none passes, that is the answer: what moves Indian
prices is public too fast for us, and the report says so plainly.

## 6. Phase 5 — Hypothesis tests (Agent 2 runs; each one declared first)

Each surviving class gets a declaration (`config/experiments/r<k>-<slug>.yaml`) with its exact
trigger (the cause and its availability), direction rule (the Call B read, or a numeric rule for
numeric causes), entry (the first bar after availability + 2 min, or the next open), exit (the atlas
horizon), sizing and the §12.4 risk engine, and costs at benchmark and adverse.
- Numeric or calendar causes: run on 2017-11..2023-12 (the back-test years) AND on Test.
- LLM-read causes: Test only.
- Bars: PROFIT_PLAN §12.5 on each window it runs on, including the coin-flip control (same triggers,
  random direction).
- Survivors are combined into one book with Track L's result if that passes too, then paper for ≥ 4
  weeks, then live at half size (the operator's decision).

## 7. Candidate mechanisms I expect the atlas to test (stated now, to be checked, not assumed)
1. **Group and peer contagion lag:** a flagship's own news (Adani Enterprises, Tata Motors) moves the
   other group or peer names later, not at the same second.
2. **Overnight global into the Indian open:** the US close and Asia are known before 09:15. Is the
   open gap complete, or does the day continue or reverse it (by the size of the gap and by VIX)?
3. **Evening flows:** FII/DII, bulk and block deals are published after the close. Next-day and 3-day
   drift after large disclosed institutional buying or selling in a name.
4. **Promoter and insider buying** (PIT/SAST): multi-day drift after the disclosure.
5. **Index inclusion and exclusion:** drift between the announcement and the effective date.
6. **Mid/small-cap orders and contracts:** multi-day under-reaction to material order wins relative to
   market cap.
7. **Scheduled macro nights** (FOMC, US CPI): the direction of the next Indian session and sector
   rotation (IT and pharma on USD/INR, OMCs on crude, banks on RBI).
8. **Results with a strong con-call tone** (C2): drift over 3-10 days after the first reaction.

## 8. Work split and order

| Step | Who | Needs | Output |
|---|---|---|---|
| 0 | Agent 2 | — | D7 quote recording applied on EC2 (operator-authorised), verified Monday |
| 1 | Agent 2 | bars | Move Ledger 2017-11..2024 + placebo |
| 2 | Agent 1 | §8 | Cause Ledger items 3.2.1-3.2.7, availability tests |
| 3 | Agent 2 | 1, 2, and Track L Dev finished (they share the daily free-token allowance) | Calls A and B, §4.2 validation pack to the head |
| 3a | Head | 3 | Audit (60), the go/no-go on the atlas |
| 4 | Agent 2 | 3a | The edge table, all-occurrences base rates |
| 5 | Head | 4 | Chooses at most 8 hypotheses and writes their declarations |
| 6 | Agent 2 | 5 | Back-test years (numeric) and Test runs, one each |

Track T1 is parked (low prior, and Agent 1 is needed for data). Track L Dev continues on its timer.

## 9. Honest expectations
The atlas will certainly answer "what moves prices" (a descriptive result with value of its own,
and the posture input for the book). Whether a TRADEABLE remainder exists after costs is open: the
evidence so far (L2: results news is priced within 5 minutes) says the instant share will be large
for company news in liquid names. The best chances are in causes that arrive outside market hours
(evening flows, overnight global, after-hours filings) and in slow-digesting ones (group contagion,
orders in mid-caps, insider buying), where the base-rate test decides. The chance that at least one
class passes both the atlas and its out-of-sample test is about 20%.
