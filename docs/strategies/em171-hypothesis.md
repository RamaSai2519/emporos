# EM-171: liquidity_thrust_v1, and curating over the history EM-132 already fetched

Committed BEFORE any of this was backtested (the commit that adds this file precedes the runs).
Plan and grid: `config/curation/plan_em171.yaml`. Judged under `config/robustness/benchmark.yaml`
unchanged (50,000 rupees, 10% per position, 2% daily loss, cost scenarios, Monte Carlo, Deflated
Sharpe, concentration, parameter neighbours, always-long baseline, walk-forward), exactly as the
eleven rejected strategies were. Nothing here may change after a result is seen.

## Two things changed this session, not one

**1. The data was never the constraint the verdict said it was.** Every EM-114/EM-118 curation ran
`--from 2025-09-22 --to 2026-09-18` — one year, ~260 trading days. `EnoughHistory` needs 500 (`
config/robustness/benchmark.yaml`), so every prior verdict was mathematically capped at INCONCLUSIVE
at best (`src/emporos/backtest/robustness/verdict.py`: any gate at `UNKNOWN`, which `EnoughHistory`
returns below its threshold, forces the overall verdict below VALIDATED even if every profit gate
passes clean). This was never about the eleven strategies being unlucky on the history-length gate —
EM-132 (committed 2026-09-21, three days before this) already fetched ten years of 5-minute bars
(2016-10-03 to 2026-09-18) for the same 29-symbol universe into a Parquet cold tier. Nobody had
pointed a curation run at it. This session does, for a strategy trying that gate for the first time.

**2. `liquidity_thrust_v1` is a new hypothesis, not a retune.** The eleven rejected strategies (
`docs/strategies/comparison.md`) all decide from price alone — a channel, an oscillator level, a
moving average — and all lost gross, before costs, in a structurally identical way: at 5,000 rupees
per position the flat brokerage plus the 5 bps marketable-limit buffer cost about 0.37% per round
trip, and none of the eleven signals gained that much before costs. `liquidity_thrust_v1` decides
from participation instead: it enters only when a bar's volume is `vol_surge_mult` times its own
trailing `vol_window`-bar average AND the bar closes within `close_location_min` of its own extreme
(a proxy for informed order flow behind the move, not a price level relative to history). It exits
on a fixed target `target_atr_mult` ATRs away (no trailing), with that multiple deliberately large so
a win is meant to clear the 0.37% hurdle by a wide margin rather than scrape past it — the "fewer,
larger trades" fix `docs/strategies/comparison.md` named as one of the two things the EM-118 numbers
pointed at (the other, longer holding periods, is out of scope: the platform is intraday cash equity
only, AGENTS.md).

## The date range: 2022-08-01 to 2026-09-18, not the full ten years

`docs/data/history.md` (EM-132) documents known data-quality issues that would corrupt any backtest
crossing them:

- **Unadjusted stock splits/scaling** read as fake ~90% overnight moves: BAJAJFINSV-EQ
  (2020-09-08), NESTLEIND-EQ (2021-12-31), TATASTEEL-EQ (2020-07-28, and an inconsistently-scaled
  span around 2022-07-26 to 2022-07-28).
- **The COVID-19 crash** (March 2020) and the five market-wide gap days it and other events caused
  (2017-07-10, 2020-03-13, 2020-03-23, 2021-02-24, plus the routine 2025-10-21 Muhurat half-hour).

2022-08-01 is safely after every dated split/scaling issue and after the COVID period, while still
giving ~4 years / ~1,000 trading days to 2026-09-18 — double the 500-day gate, with margin for the
walk-forward planner's own train/embargo/test consumption. This is a deliberate, pre-declared choice
to trade completeness for a clean series, not a search for a range that flatters a result: it was
fixed before `liquidity_thrust_v1` was backtested at all.

## What is NOT re-run

The eleven strategies in `docs/strategies/comparison.md` are untouched. Their OOS windows
(2026-01-30 to 2026-08-28) stay spent; this plan does not re-curate them over the wider range, on
this range or any other. Only `liquidity_thrust_v1`, tried here for the first time, is judged fresh.

## The grid (`config/curation/plan_em171.yaml`)

Same walk-forward shape as every prior plan (130-day train, 42-day test, 1-day embargo, Sharpe
objective on the training window only). Six candidates over two axes: how rare the surge must be
(`vol_surge_mult` 2.5 or 4.0) and how far the fixed target reaches (`target_atr_mult` 3, 4 or 5 ATRs).
`close_location_min` (0.75), `min_range_bps` (40), `stop_atr_mult` (1.0), `atr_period` (14) and
`vol_window` (20) are held fixed — not part of the search — chosen once, before any run, as
reasonable middle values (a 3x-plus volume surge is unusual for a liquid large-cap 5-minute bar; 75%
close-location is a clearly one-sided bar, not a coin flip).

## Result (2026-09-23)

**REJECTED.** Full report: `docs/strategies/em171/liquidity_thrust_v1.md`. Out-of-sample: 1,134
trades over 32 walk-forward windows, net -18,574.98 (gross -3,455.09, charges -15,119.89), profit
factor 0.320, win rate 24.1%. 0 of 32 windows profitable, 0 of 27 instruments profitable, 0% of
neighbouring parameter sets profitable. Gross P&L is negative before any cost is applied: the
signal itself has no edge here, not just a cost-drag problem like the eleven price-based rejects.

**The thing this session set out to test worked.** `EnoughHistory` (1,080 trading days >= 500) and
`EnoughTrades` (1,134 >= 150) both PASS — the first time either gate has passed in this project.
Every earlier curation was structurally capped below VALIDATED regardless of performance; this one
was not, and still failed cleanly on its own merits (`ProfitAfterCosts` FAIL, P(net > 0) = 0.000).
That is a more informative result than any of the eleven could give: a strategy tried here for the
first time, on genuinely sufficient evidence, still has no edge. It does not resurrect the
structural cost-drag story either — the always-long baseline lost less over the same 32 windows
(-21,066.37) but liquidity_thrust_v1's own gross return is negative, so its problem is the signal,
not (only) the 0.37% hurdle.

**What this rules out, and what it doesn't.** Volume-surge-plus-extreme-close, as specified here
(3-4x trailing volume, 75% close location, fixed ATR targets 3-5x), is not a source of edge on this
universe at 5-minute bars. It does not test smaller/looser surge thresholds, a trailing rather than
fixed exit, or volume divergence (falling volume into a move, rather than surging) — those would be
new, separately pre-declared hypotheses, not a re-tune of this one.

## Honest limits of the evidence, going in

- **Survivorship**: the 29-symbol universe is today's, used across the whole range (`docs/data/
  history.md`'s own caveat, inherited unchanged here).
- **A genuinely new signal family untested by anyone, on data nobody curated a strategy against
  before** — closer to a real out-of-sample test than the eleven's shared one-year window, but still
  one hypothesis, one pass. A pass here is evidence, not proof; a reject is exactly as informative as
  the eleven's were.
- **The always-long baseline still gets a vote.** Same benchmark, same `BeatsBaseline` gate: being
  long the whole extended range through whatever drift it contains is the bar this also has to clear,
  not just its own profitability.
