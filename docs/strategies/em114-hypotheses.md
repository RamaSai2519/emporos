# EM-114: five more candidate strategies, written down before any result

Committed BEFORE any of these was backtested (the commit that adds this file precedes the runs).
Plan and grids: `config/curation/plan_em114.yaml`. Judged, like the three before them, under
`config/robustness/benchmark.yaml` (50,000 rupees, 10% per position, 2% daily loss, cost scenarios,
Monte Carlo, Deflated Sharpe, concentration, parameter neighbours, always-long baseline) and
classified validated, inconclusive or rejected. Nothing here may change after a result is seen.

| strategy | hypothesis | what it does |
|---|---|---|
| `vwap_trend_v1` | intraday trends persist when price, VWAP and two EMAs agree | continuation (close beats last bar's extreme) or pullback (bounce off the fast EMA); exits on trend loss, stop, target |
| `donchian_v1` | a close beyond the session's own N-bar channel starts a move worth riding | 20/30/40-bar channel of TODAY's bars, ATR stop, ATR trailing exit |
| `ema_pullback_v1` | in a trend, a dip that holds the 9 EMA and closes back above it is a cheap entry | 9/20 EMA trend, bullish-candle confirmation, optional VWAP filter |
| `gap_go_v1` | a big overnight gap that breaks its opening range in its own direction keeps going | gap of 50-150+ bps, opening range of 3 bars, stop at the far side of the range |
| `gap_fade_v1` | a big overnight gap that fails through its opening range fills toward the old close | same gap and range, entry against the gap, target a fraction of the way back |

Six candidates each, one pre-declared grid per strategy, walk-forward with the same windows,
embargo and Sharpe objective as the first plan (`config/curation/plan.yaml`).

## What is deliberately not tested here

- **Market-direction (NIFTY) filter** for the gap and breakout strategies: the platform holds no
  index bars. Follow-up: needs index history in the candle store.
- **Donchian failed-breakout handling**: continuation only. A separate strategy, not yet built
  (EM-122 stays open for it).
- **Long and short reported separately**: every strategy allows both sides and the report shows the
  combination. Splitting them is EM-119 and EM-127.
- **Relative-strength ranking and the regime selector**: they need a cross-sectional view and a
  regime classifier that do not exist (EM-125, EM-126).
- **Fewer than 500 trading days of data**: one year is all that is held. By the benchmark's own
  rule, that alone keeps any strategy from being *validated* (it can still be rejected).

## Honest limits of the evidence

These five are new hypotheses, but they are run on the same year of data as the first three, so
the year is not "unseen" for the project as a whole, only for each strategy's own parameter choice
inside its walk-forward windows. The trial ledger counts every candidate on every window, so the
Deflated Sharpe already carries the cost of having looked at eight strategies.
