# EXP-20260923-vwap-trend-v1-em171-refresh-4fec887a

**REJECTED** | strategy | vwap-trend-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_em114.json: vwap_trend_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | cont_s10_t15, cont_s15_t20, cont_s15_t30, pull_s10_t15, pull_s15_t20, pull_s15_t30 |

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
| gross P&L | -3855.18 |
| net P&L | -14707.16 |
| expectancy per trade | n/a |
| profit factor | 0.116 |
| max drawdown (worst window) | 1.5% |
| Sharpe (annualised) | -5.491 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 815 |
| win rate | 16.6% |

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
| 0 | 2022-12-09 | 2023-01-20 | -577.18 |
| 1 | 2023-01-20 | 2023-03-03 | -558.20 |
| 2 | 2023-03-03 | 2023-04-14 | -700.02 |
| 3 | 2023-04-14 | 2023-05-26 | -693.65 |
| 4 | 2023-05-26 | 2023-07-07 | -548.83 |
| 5 | 2023-07-07 | 2023-08-18 | -647.92 |
| 6 | 2023-08-18 | 2023-09-29 | -662.37 |
| 7 | 2023-09-29 | 2023-11-10 | -578.10 |
| 8 | 2023-11-10 | 2023-12-22 | -635.89 |
| 9 | 2023-12-22 | 2024-02-02 | -609.18 |
| 10 | 2024-02-02 | 2024-03-15 | -738.01 |
| 11 | 2024-03-15 | 2024-04-26 | -659.25 |
| 12 | 2024-04-26 | 2024-06-07 | -558.15 |
| 13 | 2024-06-07 | 2024-07-19 | -658.13 |
| 14 | 2024-07-19 | 2024-08-30 | -621.37 |
| 15 | 2024-08-30 | 2024-10-11 | -576.52 |
| 16 | 2024-10-11 | 2024-11-22 | -583.60 |
| 17 | 2024-11-22 | 2025-01-03 | -529.61 |
| 18 | 2025-01-03 | 2025-02-14 | -610.19 |
| 19 | 2025-02-14 | 2025-03-28 | -637.98 |
| 20 | 2025-03-28 | 2025-05-09 | -469.57 |
| 21 | 2025-05-09 | 2025-06-20 | -666.30 |
| 22 | 2025-06-20 | 2025-08-01 | -561.53 |
| 23 | 2025-08-01 | 2025-09-12 | -625.61 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -14707.16, 95% interval -15808.89 to -13548.20, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -14707.16 as run, -27124.46 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 24 windows |
| too_few_trades | enough trades | pass | 815 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 168 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -14707.16, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_em114.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
