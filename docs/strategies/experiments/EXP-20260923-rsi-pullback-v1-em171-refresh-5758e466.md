# EXP-20260923-rsi-pullback-v1-em171-refresh-5758e466

**REJECTED** | strategy | rsi-pullback-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_plan.json: rsi_pullback_v1 curated walk-forward
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
| validation (walk-forward out-of-sample) | 2022-12-09 to 2025-09-12 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -3383.84 |
| net P&L | -14398.29 |
| expectancy per trade | n/a |
| profit factor | 0.045 |
| max drawdown (worst window) | 1.4% |
| Sharpe (annualised) | -3.707 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 826 |
| win rate | 4.4% |

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
| 0 | 2022-12-09 | 2023-01-20 | -617.38 |
| 1 | 2023-01-20 | 2023-03-03 | -568.47 |
| 2 | 2023-03-03 | 2023-04-14 | -615.03 |
| 3 | 2023-04-14 | 2023-05-26 | -588.53 |
| 4 | 2023-05-26 | 2023-07-07 | -717.27 |
| 5 | 2023-07-07 | 2023-08-18 | -574.77 |
| 6 | 2023-08-18 | 2023-09-29 | -559.82 |
| 7 | 2023-09-29 | 2023-11-10 | -612.60 |
| 8 | 2023-11-10 | 2023-12-22 | -530.45 |
| 9 | 2023-12-22 | 2024-02-02 | -583.92 |
| 10 | 2024-02-02 | 2024-03-15 | -586.58 |
| 11 | 2024-03-15 | 2024-04-26 | -645.66 |
| 12 | 2024-04-26 | 2024-06-07 | -653.80 |
| 13 | 2024-06-07 | 2024-07-19 | -603.19 |
| 14 | 2024-07-19 | 2024-08-30 | -574.70 |
| 15 | 2024-08-30 | 2024-10-11 | -529.84 |
| 16 | 2024-10-11 | 2024-11-22 | -629.41 |
| 17 | 2024-11-22 | 2025-01-03 | -655.56 |
| 18 | 2025-01-03 | 2025-02-14 | -513.52 |
| 19 | 2025-02-14 | 2025-03-28 | -608.17 |
| 20 | 2025-03-28 | 2025-05-09 | -674.31 |
| 21 | 2025-05-09 | 2025-06-20 | -516.21 |
| 22 | 2025-06-20 | 2025-08-01 | -680.06 |
| 23 | 2025-08-01 | 2025-09-12 | -559.04 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -14398.29, 95% interval -15296.87 to -13521.44, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -14398.29 as run, -27083.76 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 24 windows |
| too_few_trades | enough trades | pass | 826 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 504 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -14398.29, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_plan.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
