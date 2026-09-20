# Strategy curation

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
