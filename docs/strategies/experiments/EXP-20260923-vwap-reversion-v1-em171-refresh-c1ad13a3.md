# EXP-20260923-vwap-reversion-v1-em171-refresh-c1ad13a3

**REJECTED** | strategy | vwap-reversion-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_plan.json: vwap_reversion_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | z20_x30_s15, z20_x25_s25, z25_x30_s15, z25_x25_s25, z30_x30_s25, z30_x25_s15 |

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
| gross P&L | -4203.63 |
| net P&L | -15027.45 |
| expectancy per trade | n/a |
| profit factor | 0.077 |
| max drawdown (worst window) | 1.5% |
| Sharpe (annualised) | -4.362 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 811 |
| win rate | 14.9% |

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
| 0 | 2022-12-09 | 2023-01-20 | -606.04 |
| 1 | 2023-01-20 | 2023-03-03 | -627.36 |
| 2 | 2023-03-03 | 2023-04-14 | -579.05 |
| 3 | 2023-04-14 | 2023-05-26 | -520.95 |
| 4 | 2023-05-26 | 2023-07-07 | -634.80 |
| 5 | 2023-07-07 | 2023-08-18 | -709.22 |
| 6 | 2023-08-18 | 2023-09-29 | -597.61 |
| 7 | 2023-09-29 | 2023-11-10 | -710.86 |
| 8 | 2023-11-10 | 2023-12-22 | -656.90 |
| 9 | 2023-12-22 | 2024-02-02 | -595.70 |
| 10 | 2024-02-02 | 2024-03-15 | -711.47 |
| 11 | 2024-03-15 | 2024-04-26 | -646.75 |
| 12 | 2024-04-26 | 2024-06-07 | -598.43 |
| 13 | 2024-06-07 | 2024-07-19 | -564.07 |
| 14 | 2024-07-19 | 2024-08-30 | -645.36 |
| 15 | 2024-08-30 | 2024-10-11 | -502.96 |
| 16 | 2024-10-11 | 2024-11-22 | -685.70 |
| 17 | 2024-11-22 | 2025-01-03 | -662.95 |
| 18 | 2025-01-03 | 2025-02-14 | -734.54 |
| 19 | 2025-02-14 | 2025-03-28 | -642.57 |
| 20 | 2025-03-28 | 2025-05-09 | -619.90 |
| 21 | 2025-05-09 | 2025-06-20 | -594.16 |
| 22 | 2025-06-20 | 2025-08-01 | -532.82 |
| 23 | 2025-08-01 | 2025-09-12 | -647.28 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -15027.45, 95% interval -16159.29 to -13891.00, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -15027.45 as run, -27540.18 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 24 windows |
| too_few_trades | enough trades | pass | 811 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 336 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -15027.45, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_plan.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
