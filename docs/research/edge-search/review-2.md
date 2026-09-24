# Edge search meta-review 2 (EDGE_SEARCH_PLAN §7.5), 2026-09-25

Written by the operator's steering session after iteration 18. It supersedes review-1 §4's priority
order. Every number is from Discovery (<= 2024-12-31) or from the trial ledgers. The universe tables
are **unsigned** (magnitudes only, no direction, no P&L), computed over the D1 research names with
the seeded 30% instrument holdout (`emporos-d1-vault-2026-09-25`) never read.

## 1. What 58 screen arms say (8 cells, all rejected)

Sorted by gross per trade, the arms fall into three groups:

| Group | Arms | Gross per trade | Verdict |
|---|---|---|---|
| **Wait-then-fade** an opening dislocation (first-hour retrace >= 0.25, or 30 min on results days) | 17 | **+0.14% .. +0.43%**, rising with gap size | best: 2.5% gap, 504 trades, 0.43% gross, net +0.10% at Rs 25k, t 0.8 |
| Blind fade at the open, ORB, range compression | 29 | -0.05% .. +0.20% | nothing close |
| **Continuation** of a gap, any day type | 12 | **-0.05% .. -0.42%** | consistently wrong-signed |

Three lessons hold across every cell:

1. **The only positive gross in the program is the first-hour fade of an overnight shock, and it
   scales with the shock.** 1% gap 0.19%, 1.5% 0.26%, 2% 0.35%, 2.5% 0.43% (EM-203/204). Entering at
   the open earns nothing (0.07-0.10%, negative at 3%). The retrace is the information, not the gap.
2. **Conditioning on market state adds nothing.** The VIX gate (EM-210) selected big days but the
   fade's gross did not rise (0.16-0.28% vs 0.19-0.35% ungated). The edge is a property of the
   *name's* dislocation, not the market's regime.
3. **The binding constraint is gross per trade against a fixed cost, and the per-name threshold
   design caps it.** Raising the threshold raises gross but cuts trades toward the 300 floor on 29
   names. No arm has yet reached the S2 gross bar (0.495% adverse break-even at Rs 50k).

## 2. The wide universe changes the arithmetic (unsigned)

D1 research names with Discovery data: 176 (8 still fetching). Liquid set (>= Rs 10 crore median
daily value, >= 500 sessions, the loop's EM-214 rule): about 140.

| Slice | Median \|open -> 15:15\| | Names with median >= 0.99% |
|---|---|---|
| The audited 29 mega-caps | 0.89% | - |
| New D1 names (liquid) | **1.12%** | 95 of 140 overall |

The finding that matters most is the **cross-sectional tail**. Each session, rank all liquid names
by |gap| and keep the top K:

| Top K per day | Trades | Median \|gap\| | Median \|open -> 15:15\| | Median \|10:15 -> 15:15\| |
|---|---|---|---|---|
| 1 | 2,006 | 3.82% | 1.93% | 1.04% |
| 2 | 4,012 | 3.13% | 1.83% | 1.00% |
| 5 | 10,030 | 2.20% | 1.70% | 0.97% |

With the parent's confirmation rule (|gap| >= 1%, first-hour retrace >= 0.25), 1,995 of about 2,006
sessions have a candidate. The top one has median |gap| 2.76% and median |10:15 -> 15:15| 1.04%,
just over the S1 bar. Top-2 is 0.98%, under it. (Ranking by |gap| / 20-day sigma gives *smaller*
moves, 1.67% vs 1.93% open-to-close for K=1, so raw size is the better score for feasibility.)

**Consequence:** on 29 names the fade could be large per trade or frequent, not both. Across about
140 names, a **rank** gives the far tail on nearly every session: roughly 2,000 trades at a median
gap above the level where the mega-cap fade earned 0.43%. That is a new design, not a new
threshold. It is declared as lane L17 below.

## 3. New lane L17: daily capacity allocation (declared, `l17-daily-top-shock-fade`)

The book holds at most two Rs 50,000 positions, so capacity is scarce and signals are not. L17
spends capacity on the single most extreme confirmed dislocation across the universe each day.
Declaration: [`config/experiments/l17-daily-top-shock-fade.yaml`](../../../config/experiments/l17-daily-top-shock-fade.yaml),
committed before any directional run. 4 arms: gap floor {1.0, 2.5}% x news {all, no_results}. The
`no_results` arm tests a named mechanism: no-news shocks reverse, news shocks drift (Chan 2003).
It is not a relabelled grid.

Machinery, built by the steering session under its own ticket (see STATE): a candidate scan (the
EM-203 rules, emitting a scored candidate instead of trading every qualifying day), a
`DailyTopKSelection` that picks from **signals before fills** (a chosen order that does not fill
leaves the day empty, with no substitution), and a cross-sectional cell run that screens the
selected trades through the unchanged `Screener`, ledger and S1/S2 bars. Every arm counts toward N.

Children are fixed in advance. If gross is positive but under the bar, the only children are an L13
passive entry and an L1 size escalation to the operator. No new gap or retrace floor. If gross is
not positive, L17's fade is closed.

**Survivorship caveat specific to L17:** D1 is today's NIFTY 100 and Midcap 150. A gap-*down* fade is
a long on a name that survived into today's index, which flatters it. Report the long and short
sides separately. A pass that comes only from the long side is treated as suspect and needs the
EM-177 as-of universe before S3.

## 4. Priority order from here (supersedes review-1 §4)

1. **L17-daily-top-shock-fade** on D1, as soon as the D1 manifest (EM-214) is committed.
2. **L2-earnings-gap-fade-after-first-hour** on D1, already declared by the loop. Run it as declared.
3. **Stop running mega-cap-only cells.** L4-expected-range-filter, L4-atr-percentile-regime and
   L4-gap-fade-expected-range on the 29 names are expected to be weak (§1.2: market-state gates add
   nothing). Keep them TODO. If they run, they run on D1.
4. **Any remaining L3 or L4 cell that is a per-name threshold should be re-expressed as an L17 rank**
   when it is declared on D1. For example, the ORB volume-multiple gross rose with the multiple
   (EM-202), so "the top-1 opening-volume shock of the day" is a candidate L17 cell. Declare it
   only after `l17-daily-top-shock-fade` has a result, and then with its own rationale.
5. **D7 quote recording is still not running after 18 iterations**, and it matters more now. D1 moves
   the search into mid-caps, where real spreads are unknown and probably wider than the benchmark's
   5 bps per side. Without quotes, a mid-cap pass cannot be trusted at S6. The loop should build
   the recorder (read-only feed, Parquet), and the operator should run it on the EC2 host
   (EM-211). File it as its own ticket, not as a background wish.
6. **Screener speed:** on about 140 names a 4-arm cell reads six times the bars. The cross-sectional
   run reads each instrument once for all arms. Keep that property.

## 5. DSR and N

N = 14,086 before L17 (14,090 after its 4 arms). Under EM-206, the S4 hurdle is about z 5.6, an
annualised Sharpe of about 2.0 over 2,000 walk-forward days. Adding 4 or 40 arms barely changes it
(the hurdle grows with sqrt(log N)). So the right economy is **mechanisms, not grids**. What costs
money here is a false S2 pass, not N.

## 6. Honest outlook

- I cannot promise profit, and no rule here was loosened to make one likelier. Costs,
  thresholds and the vault are unchanged.
- The program has **one** mechanism with a positive, monotone, economically explained gross: the
  first-hour fade of an overnight shock. It has come within 0.07 percentage points of the S2 gross
  bar (0.43% vs 0.495%) on a universe too small to be selective. L17 is the most direct test of
  whether selectivity across a wide universe closes that gap. I would put its chance of passing S2
  at well under even. Its chance of passing S2, S3 and S4 together is lower still.
- If L17 and the D1 L2 child both fail, the evidence points to this conclusion: **at Rs 50,000 per
  position, with 15 bps adverse slippage per side, intraday cash-equity signals on NSE's liquid
  names do not clear costs.** The EXHAUSTED report should say so plainly. The remaining frontiers
  are, in order: D7 real spreads (the adverse scenario may be too harsh or too kind; only quotes
  can tell), the L1 size decision (at Rs 2.5L via MIS the adverse break-even is 0.38%, but leverage
  also multiplies the loss when the edge is not real, so it is for a candidate that already passed
  at Rs 50k, never to rescue one that did not), and product scope (§5 out of scope).
