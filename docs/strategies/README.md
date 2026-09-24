# Strategy curation

**Every experiment has a report with a stable id: `docs/strategies/experiments/INDEX.md`** lists all
of them, every strategy family in one table (outcome, trades, net P&L, PF, Sharpe, DSR, PBO, the
reasons that decided it). Each report (`<id>.json`, `<id>.md`) has the same sections and the same
metric rows whatever produced it, so two can be compared side by side. The reports below and in
`benchmark_50k/` and `em171/` are all indexed there, marked `BACKFILLED_NOT_PREDECLARED`: they
predate declarations, so their rationale reads "backfilled: not pre-declared" and what they did not
record is `n/a`. New experiments are declared first (`config/experiments/README.md`) and published
by `emporos backtest curate --declaration ...`. The rest of this file is the narrative of the first
curations, kept as written.

**Result: no strategy passed. None is enabled.** Three candidates were built and evaluated
honestly; each failed every check that measures profit. The plan, the criteria and the parameter
grids were committed before any result existed (commits `781e609` and `2f5750e`).

## How they were judged

- Data: a year of real 5-minute bars (2025-09-22 to 2026-09-18) for 29 liquid NSE symbols,
  529,067 bars fetched with `emporos history fetch-bars`.
- Walk-forward, rolling: tune on 130 days, then run the chosen parameters ONCE on the next,
  unseen 42 days (1-day embargo). Five out-of-sample windows, 2026-01-30 to 2026-08-28. Parameters
  are chosen only from training-window Sharpe, over a pre-declared grid of six candidates.
- The platform's own risk rules are in the loop (`RiskRuleGate`, limits from `config/risk.yaml`:
  25,000 per instrument, 3 open positions, 60,000 deployed, daily and per-strategy loss caps).
- Dated Angel One charges (the only schedule, `verified: false`, applied to earlier days with
  `--assume-earliest-fees`), marketable-limit buffer of 5 bps, conservative bar fills.
- Criteria (`SelectionCriteria`): net profit > 0; >= 150 out-of-sample trades; profit factor >= 1.2;
  profitable in >= 60% of windows and >= 50% of instruments; worst-window drawdown <= 10%; still
  profitable without its best window and with charges doubled.

## What happened

| strategy | OOS trades | gross P&L | charges | net P&L | profit factor | win rate | windows profitable |
|---|---|---|---|---|---|---|---|
| orb_v1 | 40 | -1,595.50 | 2,173.93 | -3,769.43 | 0.217 | 32.5% | 0 of 5 |
| vwap_reversion_v1 | 56 | -1,152.29 | 3,089.05 | -4,241.34 | 0.269 | 33.9% | 0 of 5 |
| rsi_pullback_v1 | 51 | -700.30 | 2,784.26 | -3,484.56 | 0.098 | 7.8% | 0 of 5 |

On 100,000 of capital these lost 3.4% to 4.2% over roughly 30 weeks. **Gross P&L is negative for
all three: they have no edge before costs, and costs (about 0.22% per round trip at these sizes,
from the 20-rupee flat brokerage per order) make it worse.** Full reports: `orb_v1.md`,
`vwap_reversion_v1.md`, `rsi_pullback_v1.md` (with `.json` records).

## Caveats that matter

- Only 40 to 56 out-of-sample trades each: the risk limits (3 positions, 25,000 each) and the
  entry rules make these strategies trade rarely on a small account. The criteria refuse to call
  so few trades evidence, so a pass was never possible on trade count alone. The gross loss, though,
  is not a small-sample artefact of one instrument (profitable in only 2 to 9 instruments).
- One year, one regime, 29 survivors of today's master (universe resolved from earliest recorded
  definitions, `--assume-current-universe`; EM-99 H8).
- The broker's history has no 15:15 to 15:29 bars for the latest ~34 sessions (EM-99 I2), so exits
  near the close use the forced square-off.
- Fee schedule unverified (EM-99 G4); fills are simulated (I5, I16).

## What NOT to do next

Do not loosen the criteria, add candidates until one passes, or re-tune on this same year: the
out-of-sample windows are now spent for these three strategies, and searching more would only find
noise. A new hypothesis needs its own pre-declared plan and data it has not seen (forward paper
trading is the honest test). Longer-holding ideas (the platform is intraday cash equity only) and
larger position sizes (flat brokerage dilutes) are the two things the numbers point to.

Reproduce: `pipenv run emporos backtest curate --only <name> --from 2025-09-22 --to 2026-09-18 --cash 100000 --assume-earliest-fees`

## The trial ledger (EM-117)

Every backtest tried in search of a result is appended to the `trial_ledger` collection, failures
included, and cannot be edited or removed (`emporos backtest trials list` counts them). `emporos
backtest curate` now records each training candidate and each test run as it goes (`--no-record`
turns that off). The three curations above and the momentum_v1 runs were backfilled from their
published records: 108 trials.

**The backfilled trials cannot yet feed a Deflated Sharpe Ratio.** The records never kept the
training-window scores, so those trials are counted (the size of the search) but carry no Sharpe,
and the spread of the trials' Sharpes, which sets how much luck to expect from the best of them,
cannot be measured. `DeflatedSharpe` reports that as a reason instead of guessing. Any strategy
curated from now on records its scores, so its DSR can be computed.

## Re-judged at the 50,000 rupee benchmark (EM-118)

`orb_v1`, `vwap_reversion_v1` and `rsi_pullback_v1` were run again under
`config/robustness/benchmark.yaml` (50,000 rupees, 5,000 per position, 2% daily loss, cost
scenarios, Monte Carlo, Deflated Sharpe, neighbouring parameters, always-long baseline). Reports:
`docs/strategies/benchmark_50k/`. The thresholds were committed before the runs (`22acad0`).

| strategy | OOS trades | gross | charges | net | verdict |
|---|---|---|---|---|---|
| orb_v1 | 167 | -915.68 | 2,220.59 | -3,136.27 | **rejected** |
| vwap_reversion_v1 | 174 | -787.65 | 2,319.43 | -3,107.08 | **rejected** |
| rsi_pullback_v1 | 171 | -864.94 | 2,273.74 | -3,138.68 | **rejected** |

All three fail the same gates: profit after costs (P(net > 0) is 0.000, the 95% interval for net
P&L is entirely below zero), profitable windows (0 of 5), parameter neighbours (0% of neighbouring
parameter sets profit) and the always-long baseline (net -3,062.66 over the same windows, which no
strategy beat). The gates that stay "unknown" would not change the outcome: history (260 trading
days against 500 needed) and concentration (there is no profit to attribute).

**The three look alike because the drag is structural.** At 5,000 per position the charges come to
about 13 per round trip and the 5 bps marketable-limit buffer on each side to about 5.5 more, so a
trade must gain about 0.37% gross to break even, and these signals gain nothing before costs. Even
with the slippage removed entirely (scenario `no_slippage`) each still loses about 2,400, which is
the charges alone. Every candidate, including the neighbours, lands within a few hundred rupees of
the same loss per window. A strategy that is to pass here needs a genuine edge per trade well above
0.37%, or fewer, larger trades; that is the constraint every new candidate should be designed
against.

The Deflated Sharpe is now computable (213 trials in the ledger, every candidate scored), and
irrelevant here: a negative Sharpe cannot beat the luck of the search.
