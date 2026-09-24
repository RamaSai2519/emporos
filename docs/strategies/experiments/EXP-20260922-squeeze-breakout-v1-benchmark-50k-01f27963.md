# EXP-20260922-squeeze-breakout-v1-benchmark-50k-01f27963

**REJECTED** | strategy | squeeze-breakout-v1-benchmark-50k | declared 2026-09-22T14:02:59+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/benchmark_50k/squeeze_breakout_v1.json: squeeze_breakout_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | tight_r15, tight_r20, tight_r30, loose_r15, loose_r20, loose_r30 |

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
| gross P&L | -950.83 |
| net P&L | -3176.33 |
| expectancy per trade | n/a |
| profit factor | 0.105 |
| max drawdown (worst window) | 1.4% |
| Sharpe (annualised) | -6.013 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 167 |
| win rate | 22.2% |

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
| 0 | 2026-01-30 | 2026-03-13 | -653.98 |
| 1 | 2026-03-13 | 2026-04-24 | -640.21 |
| 2 | 2026-04-24 | 2026-06-05 | -717.68 |
| 3 | 2026-06-05 | 2026-07-17 | -581.10 |
| 4 | 2026-07-17 | 2026-08-28 | -583.36 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -3176.33, 95% interval -3808.17 to -2541.46, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -3176.33 as run, -5730.98 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 5 windows |
| too_few_trades | enough trades | pass | 167 trades, 150 needed to validate |
| too_little_history | enough history | unknown | 260 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 423 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | fail | strategy net -3176.33, baseline -3062.66 |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/benchmark_50k/squeeze_breakout_v1.json, first committed 2026-09-22: the run predates experiment reports, so what it did not record is n/a
