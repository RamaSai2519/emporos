# EXP-20260923-gap-fade-v1-em171-refresh-32b62856

**REJECTED** | strategy | gap-fade-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_em114.json: gap_fade_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | g50_f5, g50_f10, g100_f5, g100_f10, g150_f5, g150_f10 |

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
| gross P&L | -25.29 |
| net P&L | -3938.42 |
| expectancy per trade | n/a |
| profit factor | 0.390 |
| max drawdown (worst window) | 1.7% |
| Sharpe (annualised) | -2.697 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 293 |
| win rate | 33.1% |

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
| 0 | 2022-12-09 | 2023-01-20 | -6.33 |
| 1 | 2023-01-20 | 2023-03-03 | 666.05 |
| 2 | 2023-03-03 | 2023-04-14 | -309.82 |
| 3 | 2023-04-14 | 2023-05-26 | -150.57 |
| 4 | 2023-05-26 | 2023-07-07 | -132.55 |
| 5 | 2023-07-07 | 2023-08-18 | -159.44 |
| 6 | 2023-08-18 | 2023-09-29 | -89.83 |
| 7 | 2023-09-29 | 2023-11-10 | -125.55 |
| 8 | 2023-11-10 | 2023-12-22 | -132.91 |
| 9 | 2023-12-22 | 2024-02-02 | 214.31 |
| 10 | 2024-02-02 | 2024-03-15 | -66.52 |
| 11 | 2024-03-15 | 2024-04-26 | -191.98 |
| 12 | 2024-04-26 | 2024-06-07 | -426.75 |
| 13 | 2024-06-07 | 2024-07-19 | -135.29 |
| 14 | 2024-07-19 | 2024-08-30 | -59.65 |
| 15 | 2024-08-30 | 2024-10-11 | -51.38 |
| 16 | 2024-10-11 | 2024-11-22 | -564.51 |
| 17 | 2024-11-22 | 2025-01-03 | -222.82 |
| 18 | 2025-01-03 | 2025-02-14 | -231.91 |
| 19 | 2025-02-14 | 2025-03-28 | -145.15 |
| 20 | 2025-03-28 | 2025-05-09 | -859.25 |
| 21 | 2025-05-09 | 2025-06-20 | -165.88 |
| 22 | 2025-06-20 | 2025-08-01 | -28.04 |
| 23 | 2025-08-01 | 2025-09-12 | -562.65 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -3938.42, 95% interval -5404.26 to -2464.26, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -3938.42 as run, -8472.28 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 2 of 24 windows |
| too_few_trades | enough trades | pass | 293 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.7%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 4% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 840 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -3938.42, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 5 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_em114.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
