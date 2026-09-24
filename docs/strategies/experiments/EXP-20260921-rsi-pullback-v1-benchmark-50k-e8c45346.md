# EXP-20260921-rsi-pullback-v1-benchmark-50k-e8c45346

**REJECTED** | strategy | rsi-pullback-v1-benchmark-50k | declared 2026-09-21T03:42:27+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/benchmark_50k/rsi_pullback_v1.json: rsi_pullback_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | e05_x55, e05_x70, e10_x55, e10_x70, e15_x55, e15_x70 |

Feature versions:

| feature | version |
|---|---|
| n/a |  |

## Versions

| what | value |
|---|---|
| behaviour hash (base config) | n/a |
| dataset universe hash | n/a |
| dataset calendar | n/a |
| dataset quarantine hash | n/a |
| dataset span | n/a |
| fee schedule | n/a |
| slippage (bps per side) | n/a |
| benchmark hash | n/a |
| code revision | n/a |

## Periods

| period | days |
|---|---|
| train | n/a |
| validation (walk-forward out-of-sample) | 2026-01-30 to 2026-08-28 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -864.94 |
| net P&L | -3138.68 |
| expectancy per trade | n/a |
| profit factor | 0.045 |
| max drawdown (worst window) | 1.4% |
| Sharpe (annualised) | -3.783 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 171 |
| win rate | 3.5% |

## Cost breakdown

| component | amount |
|---|---|
| brokerage | n/a |
| statutory charges | n/a |
| spread | n/a |
| slippage | n/a |
| total | n/a |
| per trade (bps of notional) | n/a |
| per trade (INR) | n/a |

## By regime

| regime | trades | net P&L | win rate |
|---|---|---|---|
| n/a |  |  |  |

## By walk-forward window

| window | first | last | net P&L |
|---|---|---|---|
| 0 | 2026-01-30 | 2026-03-13 | -628.82 |
| 1 | 2026-03-13 | 2026-04-24 | -670.97 |
| 2 | 2026-04-24 | 2026-06-05 | -654.31 |
| 3 | 2026-06-05 | 2026-07-17 | -622.96 |
| 4 | 2026-07-17 | 2026-08-28 | -561.62 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -3138.68, 95% interval -3583.50 to -2708.15, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -3138.68 as run, -5726.26 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 5 windows |
| too_few_trades | enough trades | pass | 171 trades, 150 needed to validate |
| too_little_history | enough history | unknown | 260 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 213 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | fail | strategy net -3138.68, baseline -3062.66 |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/benchmark_50k/rsi_pullback_v1.json, first committed 2026-09-21: the run predates experiment reports, so what it did not record is n/a
