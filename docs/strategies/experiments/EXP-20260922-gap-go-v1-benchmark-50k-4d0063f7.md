# EXP-20260922-gap-go-v1-benchmark-50k-4d0063f7

**REJECTED** | strategy | gap-go-v1-benchmark-50k | declared 2026-09-22T13:02:14+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/benchmark_50k/gap_go_v1.json: gap_go_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | g50_t15, g50_t25, g100_t15, g100_t25, g150_t15, g150_t25 |

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
| gross P&L | -781.40 |
| net P&L | -2791.94 |
| expectancy per trade | n/a |
| profit factor | 0.402 |
| max drawdown (worst window) | 1.5% |
| Sharpe (annualised) | -4.510 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 151 |
| win rate | 29.8% |

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
| 0 | 2026-01-30 | 2026-03-13 | -699.59 |
| 1 | 2026-03-13 | 2026-04-24 | -742.37 |
| 2 | 2026-04-24 | 2026-06-05 | -732.43 |
| 3 | 2026-06-05 | 2026-07-17 | -146.21 |
| 4 | 2026-07-17 | 2026-08-28 | -471.34 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -2791.94, 95% interval -4063.39 to -1541.42, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -2791.94 as run, -5091.90 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 5 windows |
| too_few_trades | enough trades | pass | 151 trades, 150 needed to validate |
| too_little_history | enough history | unknown | 260 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 353 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -2791.94, baseline -3062.66 |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/benchmark_50k/gap_go_v1.json, first committed 2026-09-22: the run predates experiment reports, so what it did not record is n/a
